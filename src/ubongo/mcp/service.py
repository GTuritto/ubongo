"""The MCP channel's turn helper — SDK-free so it can be unit-tested offline.

The MCP server is the fourth channel (REPL, one-shot, web, MCP) and obeys the
same contract: `send_turn` is the MCP equivalent of `oneshot.run` — it calls
the one orchestration seam (`master.handle`) and flushes the outbound queue.
No bypass: every MCP-driven turn runs classify -> plan -> execute -> govern ->
compose -> enqueue and is persisted by the Memory Agent like a typed one
(ADR-0002/0003, ADR-0015).

Two MCP-specific rules live here:
- **Approval stays human.** MCP is non-interactive; a `require_approval` turn
  returns the canned gated message with `gated=True` and is never approvable
  over this channel.
- **Reads are read-only.** `recall_view`, `daily_note_text`, and `audit_text`
  touch no write path.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TypedDict

from ubongo import channel, master, memory  # noqa: F401  -- memory registers the after_llm seam; master is the test patch target
from ubongo.delivery import queue  # noqa: F401  -- test patch target (shared module attr)
from ubongo.memory import store, vault
from ubongo.repl import DEFAULT_PERSONA, VALID_PERSONAS

logger = logging.getLogger("ubongo.mcp")

class SendResult(TypedDict):
    """ubongo_send's structured result (the tool's output schema)."""

    text: str
    ok: bool
    persona: str
    gated: bool
    # v0.5 phase 03: when gated, the decision id of the persisted pending
    # record. MCP still can't approve (ADR-0015), but the turn is no longer a
    # dead end — a human channel can `ubongo approve <decision_id>` later.
    decision_id: int | None
    requires_user_decision: bool


class RecallResult(TypedDict):
    """ubongo_recall's structured result."""

    summary: str
    recency: list[str]
    semantic: list[str]


def bootstrap() -> dict:
    """Load config + configure logging once. Starts NO background daemons —
    like one-shot and web, this is the turn path only. Delegates to the
    channel core, which also resolves the UBONGO_PROFILE knob."""
    return channel.bootstrap("mcp")


def send_turn(message: str, persona: str | None = None, auto: bool = False) -> SendResult:
    """Run one full turn for an MCP caller. Returns a plain dict (the tool's
    structured result): text, ok, persona, gated, requires_user_decision."""
    chosen = persona or DEFAULT_PERSONA
    if chosen not in VALID_PERSONAS:
        valid = ", ".join(VALID_PERSONAS)
        return {
            "text": f"Unknown persona '{chosen}'. Choose from: {valid}.",
            "ok": False, "persona": chosen, "gated": False,
            "decision_id": None, "requires_user_decision": False,
        }
    # The envelope (cProfile wrap behind the knob, master.handle, queue
    # flush) is the channel core's; this module keeps the MCP shaping only.
    response, _cpu_report = channel.run_turn(message, chosen, auto_mode=auto)
    return {
        "text": response.text,
        "ok": response.ok,
        "persona": response.persona,
        # Approval is never possible over MCP: report the gate + the id a human
        # channel can resolve, but no approve action here.
        "gated": response.approval is not None,
        "decision_id": response.approval.decision_id if response.approval else None,
        "requires_user_decision": response.requires_user_decision,
    }


def _fmt_messages(messages) -> list[str]:
    return [f"#{m.id} {m.role}: {m.content}" for m in messages]


def recall_view(query: str = "") -> RecallResult:
    """Read-only recall for the current conversation: recency window plus
    semantic hits (empty when embeddings are off), like `/recall`."""
    bootstrap()
    conversation_id = store.current_or_new_conversation(DEFAULT_PERSONA)
    ctx = store.recall(conversation_id, query or None)
    return {
        "summary": ctx.summary_text or "",
        "recency": _fmt_messages(ctx.messages),
        "semantic": _fmt_messages(ctx.semantic_messages),
    }


def daily_note_text() -> str:
    """Today's daily note, verbatim; a friendly line when none exists yet."""
    path = vault.daily_note_path(date.today())
    if not path.exists():
        return "(no daily note yet today)"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"(daily note unreadable: {exc})"


def audit_text(limit: int = 50) -> str:
    """The unified audit log tail (governance / evolution / sync / authoring)."""
    rows = vault.audit_tail(limit=limit)
    return "\n".join(rows) if rows else "(audit log empty)"
