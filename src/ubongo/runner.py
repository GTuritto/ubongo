"""Workflow runner: dispatches the agents listed in workflow.agents.

Phase 9 ships sequential mode only. Phase 12 adds parallel / competitive /
collaborative / debate / speculative — the call site (master.execute) does
not move; this module extends.

The runner threads prior findings forward: each agent sees the prior agents'
output text via AgentInput.prior_findings. Last successful agent's text
becomes the WorkflowResult.text. Per-agent failures are recorded in
agent_runs and dispatched as `agent_failed`, but the runner keeps going —
Phase 13 Repair Agent will turn that into real recovery.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from ubongo import events
from ubongo.agents.base import Agent, AgentInput, AgentResult
from ubongo.memory import store

if TYPE_CHECKING:
    from ubongo.master import Context, Workflow, WorkflowResult

logger = logging.getLogger("ubongo.runner")

LLM_FAILURE_MESSAGE = "Sorry, I couldn't reach the model. Check the logs."


def build_message_history(conv_id: int | None, current_message: str) -> tuple[str | None, list[dict]]:
    """Returns (summary_text or None, messages list ending with the current user turn).

    Master writes the user message to the store BEFORE calling the runner, so
    store.recall already includes it as the last entry. Appending current_message
    here as well duplicated the user turn in the LLM payload (review finding #2).
    For the conv_id=None branch (no persisted history yet) we still append.
    """
    history: list[dict] = []
    summary_text: str | None = None
    if conv_id is not None:
        ctx = store.recall(conv_id)
        summary_text = ctx.summary_text
        for msg in ctx.messages:
            if msg.role in ("user", "assistant"):
                history.append({"role": msg.role, "content": msg.content})
        # Defensive: if recall returned nothing or didn't end with the current
        # user message (e.g. tests that bypass append_message), still append.
        if not history or history[-1].get("content") != current_message or history[-1].get("role") != "user":
            history.append({"role": "user", "content": current_message})
    else:
        history.append({"role": "user", "content": current_message})
    return summary_text, history


class WorkflowRunner:
    def __init__(self, registry: dict[str, Agent]):
        self.registry = registry

    def execute(
        self,
        workflow: "Workflow",
        context: "Context",
        message: str,
        workflow_run_id: int | None = None,
    ) -> "WorkflowResult":
        from ubongo.master import WorkflowResult as _WR

        if workflow.execution_mode != "sequential":
            raise NotImplementedError(
                f"Phase 9: only sequential mode is implemented. Got: {workflow.execution_mode}"
            )

        summary_text, history = build_message_history(context.conversation_id, message)
        prior_findings: list[str] = []
        last_ok_result: AgentResult | None = None
        last_composer_result: AgentResult | None = None
        evaluator_confidence: float | None = None
        any_failure = False

        for agent_name in workflow.agents:
            agent = self.registry.get(agent_name)
            if agent is None:
                logger.warning("agent_not_registered", extra={"agent": agent_name})
                any_failure = True
                continue

            input = AgentInput(
                message=message,
                history=tuple(history),
                summary_text=summary_text,
                prior_findings=tuple(prior_findings),
                metadata={
                    "persona": workflow.persona,
                    "skill": workflow.skill_name,
                },
            )
            events.dispatch(
                "agent_started",
                {"agent": agent_name, "input_message_len": len(message)},
            )
            started_at = store.now_iso()
            t0 = time.monotonic()
            try:
                result = agent.run(input, context)
            except Exception as exc:  # last-ditch safety
                elapsed = int((time.monotonic() - t0) * 1000)
                logger.warning(
                    "agent_exception",
                    extra={"agent": agent_name, "cause": str(exc)},
                )
                result = AgentResult(
                    text="",
                    ok=False,
                    model=getattr(agent, "default_model", "") or None,
                    tokens_in=0,
                    tokens_out=0,
                    latency_ms=elapsed,
                    error=type(exc).__name__,
                )
            ended_at = store.now_iso()

            if workflow_run_id is not None:
                store.append_agent_run(
                    workflow_run_id=workflow_run_id,
                    agent=agent_name,
                    model=result.model,
                    input={
                        "message_len": len(message),
                        "history_len": len(history),
                        "prior_findings": len(prior_findings),
                    },
                    output={"text_len": len(result.text), "error": result.error},
                    confidence=result.confidence,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    latency_ms=result.latency_ms,
                    outcome="success" if result.ok else "failure",
                    started_at=started_at,
                    ended_at=ended_at,
                )

            if result.ok:
                events.dispatch(
                    "agent_completed",
                    {
                        "agent": agent_name,
                        "ok": True,
                        "tokens_in": result.tokens_in,
                        "tokens_out": result.tokens_out,
                    },
                )
                if result.text:
                    prior_findings.append(result.text)
                last_ok_result = result
                if getattr(agent, "composer", False):
                    last_composer_result = result
                if result.confidence is not None:
                    evaluator_confidence = result.confidence
            else:
                any_failure = True
                events.dispatch(
                    "agent_failed",
                    {"agent": agent_name, "error": result.error},
                )

        # Phase 10: prefer the last composer agent's text (the persona) over any
        # validator agent (Evaluator / Critic) that ran after it. Falls back to
        # last_ok_result so Phase 9 single-agent workflows behave unchanged when
        # no agent declares composer=True (research-only test fixtures, etc.).
        text_source = last_composer_result or last_ok_result
        if text_source is None:
            return _WR(
                text=LLM_FAILURE_MESSAGE,
                ok=False,
                tokens_in=0,
                tokens_out=0,
                model="",
                latency_ms=0,
                evaluator_confidence=evaluator_confidence,
            )

        # Per-agent token breakdown lives in agent_runs; WorkflowResult reports
        # the composer's tokens — the user-facing response is what matters here.
        return _WR(
            text=text_source.text,
            ok=not any_failure or text_source.ok,
            tokens_in=text_source.tokens_in,
            tokens_out=text_source.tokens_out,
            model=text_source.model or "",
            latency_ms=text_source.latency_ms,
            evaluator_confidence=evaluator_confidence,
        )


def default_registry() -> dict[str, Agent]:
    """Build the agent registry. Imported lazily to avoid circular imports
    (master/runner/agents form a cycle through Context type-hints).

    Phase 10: Persona Agents use bare registry names (architect, operator,
    casual) instead of the Phase-9 `persona:<name>` prefix. Evaluator and
    Critic land here too.
    """
    from ubongo.agents.critic import CriticAgent
    from ubongo.agents.evaluator import EvaluatorAgent
    from ubongo.agents.memory import default_memory_agent
    from ubongo.agents.personas import (
        ArchitectPersona,
        CasualPersona,
        OperatorPersona,
    )
    from ubongo.agents.research import ResearchAgent

    return {
        "research": ResearchAgent(),
        "memory": default_memory_agent,
        "evaluator": EvaluatorAgent(),
        "critic": CriticAgent(),
        "architect": ArchitectPersona(),
        "operator": OperatorPersona(),
        "casual": CasualPersona(),
    }
