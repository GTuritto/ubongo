"""Memory Agent: single writer for durable memory.

Phase 9 owns the assistant-message write and the vault projection. The
user-message append stays in Master (it's input, not an agent output);
orchestration tables (workflow_runs, agent_runs, governance_decisions,
notification_queue) stay with Master / runner / queue.

Enforcement is **soft** in v0.1: a ContextVar token + an `assert_memory_writer`
helper exist, but production store/vault functions do not call the assertion.
Tests opt into strict mode via the `strict_memory_writer` fixture in
`test_agents_memory.py` to verify the rule. Phase 11 (when Coding/Execution
land as real second-writer threats) revisits hard production enforcement.
"""

from __future__ import annotations

import contextvars
import logging
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

from ubongo import events
from ubongo.agents.base import AgentInput, AgentResult
from ubongo.memory import store, vault

if TYPE_CHECKING:
    from ubongo.master import Context

logger = logging.getLogger("ubongo.agents.memory")


_writer_token: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "ubongo_memory_writer", default=False
)


@contextmanager
def memory_writer():
    """Enter the single-writer region. Token resets on exit."""
    tok = _writer_token.set(True)
    try:
        yield
    finally:
        _writer_token.reset(tok)


def assert_memory_writer() -> None:
    """Raise if not inside `memory_writer()`. Test-only by default."""
    if not _writer_token.get():
        raise RuntimeError(
            "Durable-memory write outside MemoryAgent (single-writer rule)"
        )


class MemoryAgent:
    name = "memory"
    role = "single writer for messages, summaries, facts, vault, embeddings"
    default_model = ""

    def run(self, input: AgentInput, context: "Context") -> AgentResult:
        """Persist the assistant turn whose payload sits in `input.metadata`.

        Expected metadata keys: conversation_id, response_text, persona,
        model, tokens_in, tokens_out. Returns AgentResult with
        `metadata.assistant_message_id` for downstream wiring.
        """
        t0 = time.monotonic()
        md = input.metadata
        conv_id = md.get("conversation_id")
        text = md.get("response_text")
        if conv_id is None or text is None:
            return AgentResult(
                text="", ok=False, model=None,
                tokens_in=0, tokens_out=0,
                latency_ms=int((time.monotonic() - t0) * 1000),
                error="memory_missing_input",
            )
        message_id = self.commit_assistant_turn(
            conversation_id=conv_id,
            content=text,
            persona=md.get("persona"),
            model=md.get("model"),
            tokens_in=int(md.get("tokens_in", 0)),
            tokens_out=int(md.get("tokens_out", 0)),
        )
        elapsed = int((time.monotonic() - t0) * 1000)
        return AgentResult(
            text="",
            ok=True,
            model=None,
            tokens_in=0,
            tokens_out=0,
            latency_ms=elapsed,
            metadata={"assistant_message_id": message_id},
        )

    def commit_assistant_turn(
        self,
        *,
        conversation_id: int,
        content: str,
        persona: str | None,
        model: str | None,
        tokens_in: int,
        tokens_out: int,
    ) -> int:
        with memory_writer():
            return store.append_message(
                conversation_id,
                "assistant",
                content,
                persona=persona,
                model=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )

    def project_vault(self, payload: dict) -> None:
        """after_send subscriber: append the turn to today's daily note."""
        with memory_writer():
            vault._after_send_handler(payload)


default_memory_agent = MemoryAgent()


events.register("after_send", default_memory_agent.project_vault)
