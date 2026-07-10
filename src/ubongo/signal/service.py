"""The Signal channel's turn helper — transport-free so it can be unit-tested offline.

Signal is the privacy-respecting messaging channel (the platform preferred over
Telegram). It is an additive adapter over the one orchestration seam, structured
exactly like `telegram/service.py`: `handle_message` calls `channel.run_turn`
(-> `master.handle`) and the outbound queue flushes there — no bypass
(ADR-0002/0003/0024). Everything Signal-specific that does NOT touch the transport
lives here so it stays testable; `signal/client.py` is the only module that speaks
JSON-RPC to the signal-cli daemon.

The one structural difference from Telegram: the transport is a locally-run
signal-cli sidecar, not a pip SDK. That difference is entirely in `client.py`; this
module is identical in shape to the Telegram core.

Signal-specific rules:
- **Auth returns.** Only numbers in `signal.allowed_numbers` may drive the channel;
  an empty list denies everyone (fail-closed), mirroring `telegram.allowed_user_ids`.
- **No secret here.** Unlike Telegram's bot token, the Signal credential is
  signal-cli's own on-disk keystore; config carries only the bound account, the
  socket, and the allow-list.
- **Approve-later rides the existing seam (v0.7 phase 01).** A gated turn replies
  with the gated message AND the decision_id; the user resolves it over Signal with
  `/approve <id>` / `/decline <id>` (master.resume_approval — no re-implemented
  resume). `/pending` and `/grants` list state (ADR-0018/0019).
"""

from __future__ import annotations

import logging

from ubongo import channel, master  # noqa: F401  -- master is the test patch target
from ubongo.config import load_config
from ubongo.governance import approval as gov_approval
from ubongo.memory import grant_state
from ubongo.repl import DEFAULT_PERSONA

logger = logging.getLogger("ubongo.signal")

_HELP = (
    "Commands: /pending (list approvals), /approve <id>, /decline <id>, "
    "/grants (list grants). Anything else is a normal message."
)


def _normalize(value: object) -> str:
    """A Signal recipient is an E.164 number string, e.g. '+15551234567'."""
    return str(value).strip()


def allowed_numbers() -> set[str]:
    """The Signal numbers permitted to drive the channel. Empty = deny all."""
    raw = (load_config().get("signal", {}) or {}).get("allowed_numbers", []) or []
    return {_normalize(v) for v in raw if _normalize(v)}


def is_allowed(source_number: str) -> bool:
    return _normalize(source_number) in allowed_numbers()


def handle_message(text: str, source_number: str) -> str:
    """The reply for one inbound Signal message. Refuses an unauthorized sender
    (no turn runs); routes the approval/grant commands; otherwise runs a full
    governed turn and surfaces a gate (with its decision_id) when one fires."""
    if not is_allowed(source_number):
        logger.warning("signal_unauthorized", extra={"source": source_number})
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


def delivery_allowed(source_number: str) -> bool:
    """The `before_send` policy seam for Signal (minimal, mirrors Telegram).

    Default-allow; honors a single `signal.delivery_paused` config switch so the
    seam is real and testable. The full quiet-hours/holds/catch-up policy hooks in
    here later without touching the transport. Returns True when the message should
    be sent now."""
    return not bool((load_config().get("signal", {}) or {}).get("delivery_paused", False))


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
