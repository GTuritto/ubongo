"""Master Agent: the single orchestration seam for every turn.

Phase 8 wraps the existing single-persona flow in the Master Agent shape so
Phase 9 workers, Phase 10 Evaluator, and Phase 14 governance can plug into the
named-event surface without restructuring. No user-visible behavior change.

The pipeline:
    classify -> plan -> execute -> decide -> compose -> enqueue
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

from ubongo import classifier, events, router, skills
from ubongo.agents import personas
from ubongo.classifier import Classification
from ubongo.context import build_system_prompt
from ubongo.delivery import queue
from ubongo.governance.decision import Decision, decide as governance_decide
from ubongo.llm import LLMError, complete
from ubongo.memory import store

logger = logging.getLogger("ubongo.master")

_LLM_FAILURE_MESSAGE = "Sorry, I couldn't reach the model. Check the logs."


@dataclass(frozen=True)
class Context:
    conversation_id: int | None
    persona: str
    auto_mode: bool
    pending_skill: str | None


@dataclass(frozen=True)
class Workflow:
    persona: str
    model: str
    skill_name: str | None
    execution_mode: str
    agents: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowResult:
    text: str
    ok: bool
    tokens_in: int
    tokens_out: int
    model: str
    latency_ms: int


@dataclass(frozen=True)
class Response:
    text: str
    ok: bool
    persona: str
    skill_name: str | None
    delivery_token: queue.DeliveryToken


def _build_message_history(conv_id: int, current_message: str) -> tuple[str | None, list[dict]]:
    """Returns (summary_text or None, messages list ending with the current user turn)."""
    ctx = store.recall(conv_id)
    history: list[dict] = []
    for msg in ctx.messages:
        if msg.role in ("user", "assistant"):
            history.append({"role": msg.role, "content": msg.content})
    history.append({"role": "user", "content": current_message})
    return ctx.summary_text, history


class MasterAgent:
    """Single entry point for turn orchestration. Phase 8 = wrap, don't change behavior."""

    def classify(self, message: str, ctx: Context) -> Classification:
        # ctx is accepted for forward-compat (Phase 9 will thread it); ignored in Phase 8.
        return classifier.classify(message)

    def plan(self, classification: Classification, ctx: Context) -> Workflow:
        events.dispatch(
            "before_plan",
            {"classification": asdict(classification), "persona": ctx.persona, "auto_mode": ctx.auto_mode},
        )
        chosen = ctx.persona
        suggested_skill = None
        if ctx.auto_mode:
            suggested = router.route(classification)
            chosen = router.apply_hysteresis(ctx.persona, suggested, classification.confidence)
            suggested_skill = classification.suggested_skill
            logger.info(
                "classify",
                extra={
                    "intent": classification.intent,
                    "tone": classification.tone,
                    "task_type": classification.task_type,
                    "risk": classification.risk,
                    "confidence": classification.confidence,
                    "suggested": suggested,
                    "used": chosen,
                    "suggested_skill": suggested_skill,
                },
            )
        resolved_skill = skills.resolve(pinned=ctx.pending_skill, suggested=suggested_skill)
        skill_name = resolved_skill.name if resolved_skill else None
        persona = personas.get(chosen)
        workflow = Workflow(
            persona=chosen,
            model=persona.model,
            skill_name=skill_name,
            execution_mode="sequential",
            agents=(f"persona:{chosen}",),
        )
        events.dispatch("after_plan", {"workflow": asdict(workflow)})
        return workflow

    def execute(self, workflow: Workflow, ctx: Context, message: str) -> WorkflowResult:
        events.dispatch(
            "before_execute",
            {"workflow": asdict(workflow), "conversation_id": ctx.conversation_id},
        )
        persona = personas.get(workflow.persona)
        summary_text, history = _build_message_history(ctx.conversation_id, message)
        base = build_system_prompt(workflow.persona, skill=workflow.skill_name)
        system_prompt = (
            base
            if not summary_text
            else f"{base}\n\n## Conversation summary so far\n\n{summary_text}"
        )
        try:
            completion = complete(system_prompt, history, persona.model, persona.max_tokens)
        except LLMError as exc:
            logger.error(
                "llm_error",
                extra={
                    "persona": workflow.persona,
                    "model": persona.model,
                    "cause": str(exc.cause) if exc.cause else None,
                },
            )
            result = WorkflowResult(
                text=_LLM_FAILURE_MESSAGE,
                ok=False,
                tokens_in=0,
                tokens_out=0,
                model=persona.model,
                latency_ms=0,
            )
            events.dispatch("after_execute", {"workflow_result": asdict(result)})
            return result
        logger.info(
            "repl_turn",
            extra={
                "persona": workflow.persona,
                "length": len(message),
                "model": completion.model,
                "tokens_in": completion.tokens_in,
                "tokens_out": completion.tokens_out,
                "latency_ms": completion.latency_ms,
                "attempts": completion.attempts,
            },
        )
        result = WorkflowResult(
            text=completion.text,
            ok=True,
            tokens_in=completion.tokens_in,
            tokens_out=completion.tokens_out,
            model=completion.model,
            latency_ms=completion.latency_ms,
        )
        events.dispatch("after_execute", {"workflow_result": asdict(result)})
        return result

    def decide(self, classification: Classification, workflow_result: WorkflowResult, ctx: Context) -> Decision:
        events.dispatch(
            "before_govern",
            {
                "classification": asdict(classification),
                "workflow_result": asdict(workflow_result),
            },
        )
        try:
            decision = governance_decide(classification, workflow_result)
        except Exception as exc:
            logger.warning(
                "master_decide_failed",
                extra={"cause": str(exc), "intent": classification.intent},
            )
            decision = Decision(action="auto", reason="fallback_on_error")
        events.dispatch("after_govern", {"decision": asdict(decision)})
        return decision

    def compose(self, workflow: Workflow, workflow_result: WorkflowResult, ctx: Context) -> str:
        events.dispatch(
            "before_compose",
            {"workflow": asdict(workflow), "workflow_result": asdict(workflow_result)},
        )
        text = workflow_result.text
        events.dispatch(
            "after_compose",
            {"response": text, "persona": workflow.persona},
        )
        return text

    def handle(
        self,
        message: str,
        persona_name: str,
        auto_mode: bool = False,
        pending_skill: str | None = None,
    ) -> Response:
        """End-to-end orchestration. Returns a Response; caller prints + flushes."""
        ctx = Context(
            conversation_id=None,
            persona=persona_name,
            auto_mode=auto_mode,
            pending_skill=pending_skill,
        )
        classification = self.classify(message, ctx)
        workflow = self.plan(classification, ctx)
        chosen = workflow.persona

        conv_id = store.current_or_new_conversation(chosen)
        user_msg_id = store.append_message(conv_id, "user", message, persona=chosen)
        ctx = Context(
            conversation_id=conv_id,
            persona=chosen,
            auto_mode=auto_mode,
            pending_skill=pending_skill,
        )

        result = self.execute(workflow, ctx, message)

        assistant_msg_id = None
        if result.ok:
            assistant_msg_id = store.append_message(
                conv_id,
                "assistant",
                result.text,
                persona=chosen,
                model=result.model,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
            )
        ts_now = store.now_iso()
        store.upsert_session(
            active_persona=chosen,
            current_conversation_id=conv_id,
            last_message_at=ts_now,
            auto_mode=auto_mode,
        )

        decision = self.decide(classification, result, ctx)
        # decision.action is logged but does NOT alter flow in Phase 8 (always "auto").

        text = self.compose(workflow, result, ctx)

        after_send_payload: dict | None = None
        if result.ok:
            after_send_payload = {
                "user_message": message,
                "response": text,
                "persona": chosen,
                "auto_routed": auto_mode,
                "conversation_id": conv_id,
                "user_message_id": user_msg_id,
                "assistant_message_id": assistant_msg_id,
                "ts": ts_now,
            }
        token = queue.enqueue_for_delivery(
            text,
            source="response" if result.ok else "error",
            after_send_payload=after_send_payload,
            metadata={
                "persona": chosen,
                "auto_routed": auto_mode,
                "conversation_id": conv_id,
                "assistant_message_id": assistant_msg_id,
                "decision_action": decision.action,
            },
        )
        return Response(
            text=text,
            ok=result.ok,
            persona=chosen,
            skill_name=workflow.skill_name,
            delivery_token=token,
        )


default_master = MasterAgent()


def handle(
    message: str,
    persona_name: str,
    auto_mode: bool = False,
    pending_skill: str | None = None,
) -> Response:
    return default_master.handle(message, persona_name, auto_mode, pending_skill)
