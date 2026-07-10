"""The Signal channel's turn helper — transport-free so it can be unit-tested offline.

Signal is the privacy-respecting messaging channel (the platform preferred over
Telegram). It is an additive adapter over the one orchestration seam, structured
exactly like `telegram/service.py`: `handle_message` calls `channel.run_turn`
(-> `master.handle`) and the outbound queue flushes there — no bypass
(ADR-0002/0003; the channel decision is drafted in ADR-0024). Everything
Signal-specific that does NOT touch the transport lives here so it stays testable;
`signal/client.py` is the only module that speaks JSON-RPC to the signal-cli daemon.

The one structural difference from Telegram: the transport is a locally-run
signal-cli sidecar, not a pip SDK. That difference is entirely in `client.py`; this
module is identical in shape to the Telegram core.

Signal-specific rules:
- **Auth returns.** Only numbers in `signal.allowed_numbers` may drive the channel;
  an empty list denies everyone (fail-closed), mirroring `telegram.allowed_user_ids`.
- **No secret here.** Unlike Telegram's bot token, the Signal credential is
  signal-cli's own on-disk keystore; config carries only the bound account, the
  socket, and the allow-list.

Phase 00 scope (v0.7/00): auth + a normal turn round-trip. A gated turn's text is
surfaced with a pointer to the existing cross-channel approval surface; the
`/approve|/decline|/pending|/grants` command router over Signal is Phase 01.
"""

from __future__ import annotations

import logging

from ubongo import channel  # noqa: F401
from ubongo.config import load_config
from ubongo.repl import DEFAULT_PERSONA

logger = logging.getLogger("ubongo.signal")


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
    (no turn runs); otherwise runs a full governed turn and, when a gate fires,
    surfaces the gated text and points at the cross-channel approval surface
    (approve-later *over Signal* is Phase 01)."""
    if not is_allowed(source_number):
        logger.warning("signal_unauthorized", extra={"source": source_number})
        return "Not authorized."

    text = (text or "").strip()
    if not text:
        return "Send a message to start a turn."

    # A normal turn — the one orchestration seam, no bypass.
    response, _ = channel.run_turn(text, DEFAULT_PERSONA, auto_mode=True)
    if response.approval is not None:
        return (
            f"{response.text}\n\n"
            f"This turn needs approval (decision #{response.approval.decision_id}). "
            f"Resolve it from any channel with `ubongo approve "
            f"{response.approval.decision_id}` (or `ubongo decline "
            f"{response.approval.decision_id}`). Approving over Signal lands in the "
            f"next phase."
        )
    return response.text


def delivery_allowed(source_number: str) -> bool:
    """The `before_send` policy seam for Signal (minimal, mirrors Telegram).

    Default-allow; honors a single `signal.delivery_paused` config switch so the
    seam is real and testable. The full quiet-hours/holds/catch-up policy hooks in
    here later without touching the transport. Returns True when the message should
    be sent now."""
    return not bool((load_config().get("signal", {}) or {}).get("delivery_paused", False))
