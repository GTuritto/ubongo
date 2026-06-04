from __future__ import annotations

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
        from ubongo.memory import store

        try:
            source = str(note_path.relative_to(_vault_root()))
        except ValueError:
            source = note_path.name
        for text in texts:
            for target in parse_wikilinks(text):
                store.upsert_vault_link(source, target, link_type="wikilink")
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
    return _vault_root() / "system" / "evolution-audit.md"


def append_audit(timestamp: str, line: str) -> Path:
    """Append one promotion-decision row to vault/system/evolution-audit.md
    (Phase 19g). Created with a header on first decision. `line` is a
    pre-formatted markdown list item; the timestamp is prefixed."""
    path = audit_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", encoding="utf-8") as f:
        if is_new:
            f.write("---\ntags: [ubongo, evolution, audit]\n---\n\n"
                    "# Evolution Promotion Audit\n\n"
                    "One row per promotion decision (approve / reject / rollback).\n\n")
        f.write(f"- {timestamp} — {line}\n")
    logger.info("evolution_audit_written", extra={"path": str(path), "new_file": is_new})
    return path


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
