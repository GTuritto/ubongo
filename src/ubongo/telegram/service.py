"""The Telegram channel's turn helper — SDK-free so it can be unit-tested offline.

Telegram is the fifth channel (REPL, one-shot, web, MCP, Telegram) and the first
cloud-relayed one. It obeys the same contract: `handle_message` calls the one
orchestration seam via `channel.run_turn` (-> `master.handle`) and the outbound
queue flushes there — no bypass (ADR-0002/0003/0020). Everything Telegram-specific
that does NOT touch the network lives here so it stays testable; `telegram/bot.py`
is the only module that imports httpx and speaks the Bot API.

Telegram-specific rules:
- **Auth returns.** Only ids in `telegram.allowed_user_ids` may drive the bot;
  an empty list denies everyone (fail-closed). The first channel since v0.1 with
  real auth.
- **Approve-later rides the Phase-03 seam.** A gated turn replies with the gated
  message AND the decision_id; the user resolves it with `/approve <id>` /
  `/decline <id>` (master.resume_approval). `/pending` and `/grants` list state.
"""

from __future__ import annotations

import logging

from ubongo import channel, master  # noqa: F401  -- master is the test patch target
from ubongo.config import load_config
from ubongo.governance import approval as gov_approval
from ubongo.memory import grant_state
from ubongo.repl import DEFAULT_PERSONA

logger = logging.getLogger("ubongo.telegram")

_HELP = (
    "Commands: /pending (list approvals), /approve <id>, /decline <id>, "
    "/grants (list grants). Anything else is a normal message."
)


def allowed_user_ids() -> set[int]:
    """The Telegram user ids permitted to drive the bot. Empty = deny all."""
    raw = (load_config().get("telegram", {}) or {}).get("allowed_user_ids", []) or []
    out: set[int] = set()
    for v in raw:
        try:
            out.add(int(v))
        except (TypeError, ValueError):
            continue
    return out


def is_allowed(user_id: int) -> bool:
    return int(user_id) in allowed_user_ids()


def handle_message(text: str, user_id: int) -> str:
    """The reply for one inbound Telegram message. Refuses an unauthorized user
    (no turn runs); routes the approval/grant commands; otherwise runs a full
    governed turn and surfaces a gate (with its decision_id) when one fires."""
    if not is_allowed(user_id):
        logger.warning("telegram_unauthorized", extra={"user_id": user_id})
        return "Not authorized."

    text = (text or "").strip()
    if not text:
        return _HELP

    head, _, rest = text.partition(" ")
    head = head.lower()
    arg = rest.strip()

    if head in ("/start", "/help"):
        return _HELP
    if head == "/pending":
        return _render_pending()
    if head == "/grants":
        return _render_grants()
    if head in ("/approve", "/decline"):
        return _resolve(head, arg)

    # A normal turn — the one orchestration seam, no bypass.
    response, _ = channel.run_turn(text, DEFAULT_PERSONA, auto_mode=True)
    if response.approval is not None:
        return (
            f"{response.text}\n\n"
            f"Reply /approve {response.approval.decision_id} to proceed, "
            f"or /decline {response.approval.decision_id}."
        )
    return response.text


def delivery_allowed(user_id: int) -> bool:
    """The `before_send` policy seam for Telegram (v0.5 phase 04, minimal).

    Default-allow. This is where the later notification-policy engine (quiet
    hours, holds, catch-up) hooks in to suppress or defer a delivery without
    touching the core — for now it honors a single config switch,
    `telegram.delivery_paused`, so the seam is real and testable. Returns True
    when the message should be sent now."""
    return not bool((load_config().get("telegram", {}) or {}).get("delivery_paused", False))


def _resolve(command: str, arg: str) -> str:
    try:
        decision_id = int(arg)
    except (TypeError, ValueError):
        return f"Usage: {command} <id>. {_HELP}"
    pending = gov_approval.get_pending(decision_id)
    if pending is None or pending.status != "pending":
        return f"No pending approval #{decision_id} (unknown or already resolved)."
    if command == "/approve":
        resumed = master.resume_approval(decision_id, "y")
        return (f"Approved #{decision_id}.\n\n{resumed.text}"
                if resumed is not None else f"Approved #{decision_id}.")
    master.resume_approval(decision_id, "n")
    return f"Declined #{decision_id}; nothing was done."


def _render_pending() -> str:
    rows = gov_approval.list_pending()
    if not rows:
        return "No pending approvals."
    lines = [f"Pending approvals ({len(rows)}):"]
    for p in rows:
        snippet = p.message.strip().replace("\n", " ")
        if len(snippet) > 60:
            snippet = snippet[:57] + "..."
        lines.append(f"  #{p.decision_id}  {p.persona}: {snippet}")
    lines.append("Approve with /approve <id> (or /decline <id>).")
    return "\n".join(lines)


def _render_grants() -> str:
    rows = grant_state.active_grants()
    if not rows:
        return "No active grants."
    lines = [f"Active grants ({len(rows)}):"]
    for g in rows:
        lines.append(f"  #{g['id']}  {g['capability_class']}  ({g['consequence_class']})")
    return "\n".join(lines)
