"""Master Agent: the single orchestration seam for every turn.

Phase 9 delegates execute() to the WorkflowRunner; agents (Research, Memory,
PersonaAgent wrappers) carry out the LLM work. Master remains the place that
classifies, plans the workflow, gates governance, composes, persists the
workflow_runs / governance_decisions rows, calls MemoryAgent for the
assistant message, and enqueues the response.

The pipeline:
    classify -> plan -> execute (runner) -> decide -> compose -> commit -> enqueue
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass

from ubongo import classifier, events, router, skills
from ubongo.agents import personas
from ubongo.agents.memory import default_memory_agent
from ubongo.classifier import Classification
from ubongo.delivery import queue
from ubongo.governance.decision import Decision, decide as governance_decide
from ubongo.memory import store

logger = logging.getLogger("ubongo.master")


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


_PERSONA_DEFAULT_WORKFLOW: dict[str, str] = {
    "architect": "technical_deep",
    "operator": "quick_action",
    "casual": "casual_reply",
}


def _resolve_workflow_name(
    chosen_persona: str,
    suggested_workflow: str | None,
    auto_mode: bool,
) -> str:
    """Decide which workflow to run.

    auto_mode + hysteresis kept the suggested persona  -> use suggested workflow.
    auto_mode + hysteresis flipped to a different one  -> persona's default.
    auto_mode off                                       -> persona's default.
    """
    if auto_mode and suggested_workflow is not None:
        if router.workflow_persona(suggested_workflow) == chosen_persona:
            return suggested_workflow
    return _PERSONA_DEFAULT_WORKFLOW.get(chosen_persona, "casual_reply")


class MasterAgent:
    """Single entry point for turn orchestration."""

    def __init__(self) -> None:
        # Runner + registry are built lazily on first dispatch so module import
        # does not require a valid config (Phase 0 missing-key scenario).
        self._runner = None

    def _ensure_runner(self):
        if self._runner is None:
            from ubongo.runner import WorkflowRunner, default_registry

            self._runner = WorkflowRunner(default_registry())
        return self._runner

    def classify(self, message: str, ctx: Context) -> Classification:
        return classifier.classify(message)

    def plan(self, classification: Classification, ctx: Context) -> Workflow:
        events.dispatch(
            "before_plan",
            {
                "classification": asdict(classification),
                "persona": ctx.persona,
                "auto_mode": ctx.auto_mode,
            },
        )
        chosen = ctx.persona
        suggested_workflow_name: str | None = None
        suggested_skill = None
        if ctx.auto_mode:
            suggested_workflow_name = router.route_workflow(classification)
            suggested_persona = router.workflow_persona(suggested_workflow_name)
            chosen = router.apply_hysteresis(ctx.persona, suggested_persona, classification.confidence)
            suggested_skill = classification.suggested_skill
            logger.info(
                "classify",
                extra={
                    "intent": classification.intent,
                    "tone": classification.tone,
                    "task_type": classification.task_type,
                    "risk": classification.risk,
                    "confidence": classification.confidence,
                    "suggested_workflow": suggested_workflow_name,
                    "suggested_persona": suggested_persona,
                    "used": chosen,
                    "suggested_skill": suggested_skill,
                },
            )
        resolved_skill = skills.resolve(pinned=ctx.pending_skill, suggested=suggested_skill)
        skill_name = resolved_skill.name if resolved_skill else None
        workflow_name = _resolve_workflow_name(chosen, suggested_workflow_name, ctx.auto_mode)
        agents = router.workflow_agents(workflow_name)
        mode = router.workflow_mode(workflow_name)
        persona = personas.get(chosen)
        workflow = Workflow(
            persona=chosen,
            model=persona.model,
            skill_name=skill_name,
            execution_mode=mode,
            agents=agents,
        )
        events.dispatch("after_plan", {"workflow": asdict(workflow)})
        return workflow

    def execute(
        self,
        workflow: Workflow,
        ctx: Context,
        message: str,
        workflow_run_id: int | None = None,
    ) -> WorkflowResult:
        events.dispatch(
            "before_execute",
            {"workflow": asdict(workflow), "conversation_id": ctx.conversation_id},
        )
        runner = self._ensure_runner()
        result = runner.execute(workflow, ctx, message, workflow_run_id=workflow_run_id)
        events.dispatch("after_execute", {"workflow_result": asdict(result)})
        return result

    def decide(
        self,
        classification: Classification,
        workflow_result: WorkflowResult,
        ctx: Context,
    ) -> Decision:
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
        started_at = store.now_iso()
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

        # Phase 9e: INSERT workflow_runs with outcome='in_progress' before the
        # runner so it can FK-link agent_runs immediately. Patch outcome after.
        workflow_run_id = store.append_workflow_run(
            conversation_id=conv_id,
            message_id=user_msg_id,
            classification=asdict(classification),
            workflow=asdict(workflow),
            execution_mode=workflow.execution_mode,
            outcome="in_progress",
            started_at=started_at,
        )

        result = self.execute(workflow, ctx, message, workflow_run_id=workflow_run_id)

        assistant_msg_id = None
        if result.ok:
            mem_started = store.now_iso()
            mem_t0 = time.monotonic()
            assistant_msg_id = default_memory_agent.commit_assistant_turn(
                conversation_id=conv_id,
                content=result.text,
                persona=chosen,
                model=result.model,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
            )
            mem_elapsed_ms = int((time.monotonic() - mem_t0) * 1000)
            store.append_agent_run(
                workflow_run_id=workflow_run_id,
                agent="memory",
                model=None,
                input={"content_len": len(result.text), "conversation_id": conv_id},
                output={"assistant_message_id": assistant_msg_id},
                confidence=None,
                tokens_in=0,
                tokens_out=0,
                latency_ms=mem_elapsed_ms,
                outcome="success",
                started_at=mem_started,
                ended_at=store.now_iso(),
            )
        ts_now = store.now_iso()
        store.upsert_session(
            active_persona=chosen,
            current_conversation_id=conv_id,
            last_message_at=ts_now,
            auto_mode=auto_mode,
        )

        store.update_workflow_run_outcome(
            workflow_run_id,
            outcome="success" if result.ok else "failure",
            ended_at=ts_now,
        )

        decision = self.decide(classification, result, ctx)
        decision_id = store.append_governance_decision(
            workflow_run_id=workflow_run_id,
            intent=classification.intent,
            risk=classification.risk,
            confidence=classification.confidence,
            reversibility=None,
            action=decision.action,
        )

        logger.info(
            "master_decision",
            extra={
                "intent": classification.intent,
                "tone": classification.tone,
                "task_type": classification.task_type,
                "risk": classification.risk,
                "confidence": classification.confidence,
                "persona": chosen,
                "skill": workflow.skill_name,
                "execution_mode": workflow.execution_mode,
                "action": decision.action,
                "workflow_run_id": workflow_run_id,
                "decision_id": decision_id,
                "conversation_id": conv_id,
                "agents": list(workflow.agents),
            },
        )

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
                "workflow_run_id": workflow_run_id,
                "decision_id": decision_id,
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
                "workflow_run_id": workflow_run_id,
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
