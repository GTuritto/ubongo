from __future__ import annotations

import logging
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


events.register("after_send", _after_send_handler)
