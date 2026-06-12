from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime
from datetime import time as time_type
from pathlib import Path

from ubongo import events
from ubongo.config import load_config

logger = logging.getLogger("ubongo.memory.vault")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_vault_root_override: Path | None = None


def _vault_root() -> Path:
    if _vault_root_override is not None:
        return _vault_root_override
    config = load_config()
    vault_cfg = config.get("vault", {}) or {}
    raw = vault_cfg.get("path", "./vault")
    p = Path(raw)
    if not p.is_absolute():
        p = (_REPO_ROOT / p).resolve()
    return p


def _daily_subdir() -> str:
    config = load_config()
    return config.get("vault", {}).get("daily_notes_subdir", "daily")


def set_vault_root(path: Path | None) -> None:
    """Override the vault root (used by tests with tempfiles)."""
    global _vault_root_override
    _vault_root_override = path


def daily_note_path(d: date_type) -> Path:
    return _vault_root() / _daily_subdir() / f"{d.isoformat()}.md"


# --- Phase 21: content hashing + system-write tracking ----------------------


def file_hash(path: Path) -> str:
    """Short stable hash of a file's bytes (empty for a missing file)."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def vault_relpath(path: Path) -> str:
    try:
        return str(path.relative_to(_vault_root()))
    except ValueError:
        return path.name


def _record_system_write(path: Path) -> None:
    """Record the hash the system just wrote so the watcher can distinguish its
    own writes from external user edits. Best-effort."""
    try:
        from ubongo.memory import index_state
        from ubongo.memory import store
        index_state.record_vault_write(vault_relpath(path), file_hash(path))
    except Exception as exc:
        logger.warning("vault_state_record_failed", extra={"error": str(exc)[:160]})


def _frontmatter(d: date_type) -> str:
    return (
        "---\n"
        f"date: {d.isoformat()}\n"
        "tags: [ubongo, daily]\n"
        "---\n\n"
        f"# {d.isoformat()}\n\n"
    )


def _entry(
    t: time_type,
    user_message: str,
    response: str,
    persona: str,
    auto_routed: bool,
) -> str:
    suffix = " (auto)" if auto_routed else ""
    return (
        f"## {t.isoformat(timespec='seconds')} — {persona}{suffix}\n\n"
        f"**You:**\n\n{user_message}\n\n"
        f"**Ubongo:**\n\n{response}\n\n"
    )


_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")


def parse_wikilinks(text: str) -> list[str]:
    """Return the `[[target]]` link targets in `text`, de-duplicated in order.
    Handles `[[target|alias]]` and `[[target#heading]]` (target only)."""
    seen: list[str] = []
    for m in _WIKILINK_RE.finditer(text or ""):
        target = m.group(1).strip()
        if target and target not in seen:
            seen.append(target)
    return seen


def _index_wikilinks(note_path: Path, *texts: str) -> None:
    """Phase 20: upsert vault_links for every `[[wikilink]]` found in the given
    texts, sourced from `note_path`. Best-effort — never blocks the note write."""
    try:
        from ubongo.memory import index_state

        try:
            source = str(note_path.relative_to(_vault_root()))
        except ValueError:
            source = note_path.name
        for text in texts:
            for target in parse_wikilinks(text):
                index_state.upsert_vault_link(source, target, link_type="wikilink")
    except Exception as exc:
        logger.warning("vault_link_index_failed", extra={"error": str(exc)[:160]})


def append_to_daily_note(
    d: date_type,
    t: time_type,
    user_message: str,
    response: str,
    persona: str,
    *,
    auto_routed: bool = False,
) -> Path:
    path = daily_note_path(d)
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", encoding="utf-8") as f:
        if is_new:
            f.write(_frontmatter(d))
        f.write(_entry(t, user_message, response, persona, auto_routed))
    _index_wikilinks(path, user_message, response)
    _record_system_write(path)
    logger.info(
        "vault_note_written",
        extra={
            "path": str(path),
            "persona": persona,
            "auto_routed": auto_routed,
            "new_file": is_new,
        },
    )
    return path


def audit_log_path() -> Path:
    """The unified audit log (Phase 21c). Phase 19's evolution-only audit
    redirects here, so governance, evolution, and sync decisions share one
    Obsidian-readable file."""
    return _vault_root() / "system" / "audit.md"


_AUDIT_CATEGORIES = ("governance", "evolution", "sync", "authoring", "mcp")


def append_audit_entry(category: str, line: str, *, timestamp: str | None = None) -> Path:
    """Append one categorized row to vault/system/audit.md (Phase 21c). Each row
    is `- <ts> [<category>] <line>` so `/audit` can filter by category. Best-
    effort header on first write. The category is validated loosely (unknown
    categories are still written, just logged)."""
    from ubongo.memory import store

    if category not in _AUDIT_CATEGORIES:
        logger.warning("audit_unknown_category", extra={"category": category})
    ts = timestamp or store.now_iso()
    path = audit_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", encoding="utf-8") as f:
        if is_new:
            f.write("---\ntags: [ubongo, audit]\n---\n\n"
                    "# Audit Log\n\n"
                    "Unified governance + evolution + sync audit. "
                    "One row per decision/event: `- <ts> [<category>] <detail>`.\n\n")
        f.write(f"- {ts} [{category}] {line}\n")
    _record_system_write(path)
    logger.info("audit_written", extra={"category": category, "new_file": is_new})
    return path


def append_audit(timestamp: str, line: str) -> Path:
    """Phase 19 back-compat shim: route promotion-decision rows into the unified
    audit log under the `evolution` category."""
    return append_audit_entry("evolution", line, timestamp=timestamp)


def audit_tail(category: str | None = None, limit: int = 20) -> list[str]:
    """Return the last `limit` audit rows (entry lines starting with '- '),
    optionally filtered to a `[category]`. Newest last."""
    path = audit_log_path()
    if not path.exists():
        return []
    try:
        rows = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")]
    except OSError:
        return []
    if category:
        rows = [ln for ln in rows if f"[{category}]" in ln]
    return rows[-limit:] if limit > 0 else rows


@dataclass(frozen=True)
class VaultSnippet:
    path: str
    snippet: str


def search_daily_notes(
    query: str,
    max_snippets: int = 5,
    max_files: int = 30,
    window: int = 200,
) -> list[VaultSnippet]:
    """Phase-9 retrieval: grep daily notes for any content word in `query`.

    Walks the latest `max_files` daily-note files (by mtime, newest first) and
    returns up to `max_snippets` (path, snippet) hits, with `window`-char
    context around the first match per file. Intentionally dumb; Phase 20
    swaps in embeddings.
    """
    if not query.strip():
        return []
    daily_dir = _vault_root() / _daily_subdir()
    if not daily_dir.exists():
        return []

    words = [w.lower() for w in _tokenize(query) if len(w) >= 3 and w.lower() not in _STOPWORDS]
    if not words:
        return []

    files = sorted(
        (p for p in daily_dir.glob("*.md") if p.is_file() and not p.name.startswith(".")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:max_files]

    snippets: list[VaultSnippet] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        lower = text.lower()
        for w in words:
            idx = lower.find(w)
            if idx >= 0:
                start = max(0, idx - window // 2)
                end = min(len(text), idx + window // 2)
                rel = path.relative_to(_vault_root())
                snippets.append(VaultSnippet(path=str(rel), snippet=text[start:end].strip()))
                break
        if len(snippets) >= max_snippets:
            break
    return snippets


_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "if", "then", "so", "for", "of",
    "to", "in", "on", "at", "by", "with", "from", "as", "is", "are", "was",
    "were", "be", "been", "being", "do", "does", "did", "this", "that",
    "these", "those", "it", "its", "we", "you", "i", "they", "he", "she",
    "what", "which", "who", "when", "where", "why", "how", "can", "could",
    "should", "would", "will", "shall", "may", "might", "must", "about",
    "into", "out", "over", "under", "again", "further", "than", "too",
    "very", "just", "also",
})


def _tokenize(text: str) -> list[str]:
    import re
    return re.findall(r"[A-Za-z0-9_]+", text)


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _after_send_handler(payload: dict) -> None:
    """Default after_send subscriber: append the turn to today's daily note."""
    user_message = payload.get("user_message")
    response = payload.get("response")
    persona = payload.get("persona") or "casual"
    auto_routed = bool(payload.get("auto_routed", False))
    ts = payload.get("ts")
    if not user_message or not response or not ts:
        return
    when = _parse_iso(ts)
    append_to_daily_note(
        when.date(),
        when.time().replace(microsecond=0),
        user_message,
        response,
        persona,
        auto_routed=auto_routed,
    )


# Phase 9c: registration moved to agents.memory so the MemoryAgent owns the
# write path (single-writer rule). The handler body lives here as
# `_after_send_handler` and is invoked through MemoryAgent.project_vault.
