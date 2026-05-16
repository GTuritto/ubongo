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
from dataclasses import asdict, dataclass, replace

from ubongo import classifier, events, router, skills
from ubongo.agents import personas
from ubongo.agents.memory import default_memory_agent
from ubongo.classifier import Classification
from ubongo.config import load_governance
from ubongo.governance import approval as governance_approval
from ubongo.delivery import queue
from ubongo.governance.decision import Action, Decision, decide as governance_decide
from ubongo.memory import store
from ubongo.memory.write_buffer import workflow_buffer

logger = logging.getLogger("ubongo.master")


@dataclass(frozen=True)
class Context:
    conversation_id: int | None
    persona: str
    auto_mode: bool
    pending_skill: str | None
    # Phase 12g: one-shot workflow override (set via /mode <workflow>).
    # Cleared after the next turn (mirrors pending_skill).
    pending_workflow: str | None = None


@dataclass(frozen=True)
class Workflow:
    persona: str
    model: str
    skill_name: str | None
    execution_mode: str
    agents: tuple[str, ...]
    # Phase 12 mode-specific config. Optional; carried into workflow_runs.workflow
    # JSON via asdict() so the trace records the mode parameters.
    rounds: int | None = None       # 12d: debate mode
    timeout_s: int | None = None    # 12e: speculative mode


@dataclass(frozen=True)
class WorkflowResult:
    text: str
    ok: bool
    tokens_in: int
    tokens_out: int
    model: str
    latency_ms: int
    evaluator_confidence: float | None = None


@dataclass(frozen=True)
class Response:
    text: str
    ok: bool
    persona: str
    skill_name: str | None
    delivery_token: queue.DeliveryToken
    # Phase 13f: when Repair gave up on a failure, the caller (REPL / one-shot)
    # may want to surface a y/n retry prompt. `repair_summary` is None when no
    # repair fired; otherwise carries {attempts, last_kind, last_strategy,
    # last_error, failing_agent} extracted from repair_runs.
    requires_user_decision: bool = False
    repair_summary: dict | None = None
    # Phase 15: set when governance returned `require_approval`. Carries the
    # ApprovalRequest as a dict ({decision_id, summary, why}); the REPL prompts
    # y/n/why off it. None on every non-gated turn.
    approval: dict | None = None


_PERSONA_DEFAULT_WORKFLOW: dict[str, str] = {
    "architect": "technical_deep",
    "operator": "quick_action",
    "casual": "casual_reply",
}

_REJECT_MESSAGE = (
    "I'm not confident enough in my answer to give it. "
    "Try rephrasing or breaking the question down."
)
_CLARIFICATION_MESSAGE = (
    "I need a bit more detail before I can do that. Tell me specifically "
    "what you want and I'll take it from there."
)
_APPROVAL_REQUIRED_MESSAGE = (
    "This looks destructive or high-risk, so I'm not proceeding without "
    "explicit approval. (The interactive approval flow lands in Phase 15.)"
)

# decision.action -> the message that replaces the response when a turn is
# gated. A gated turn is still delivered as the assistant message (ok=True) so
# the trace, vault and recall stay coherent.
_GATED_MESSAGES: dict[str, str] = {
    Action.REJECT.value: _REJECT_MESSAGE,
    Action.ASK_CLARIFICATION.value: _CLARIFICATION_MESSAGE,
    Action.REQUIRE_APPROVAL.value: _APPROVAL_REQUIRED_MESSAGE,
}


def _critic_band() -> tuple[float, float]:
    """Borderline evaluator-confidence band that triggers a Critic re-dispatch.

    Phase 10 had these as the module constants CRITIC_LOW / CRITIC_HIGH;
    Phase 14 moved them into governance.yaml::thresholds.critic_band.
    """
    band = (load_governance().get("thresholds", {}) or {}).get("critic_band", [0.2, 0.6])
    try:
        return float(band[0]), float(band[1])
    except (TypeError, ValueError, IndexError):
        return 0.2, 0.6

# Phase 13f: shown when Repair exhausted its strategy ladder.
# {attempts}/{last_kind}/{last_strategy}/{failing_agent}/{last_error} fill in
# from the repair_summary dict; missing fields render as "—".
_REPAIR_EXHAUSTED_TEMPLATE = (
    "I couldn't recover from a {last_kind} in the {failing_agent} step "
    "after {attempts} repair attempt(s). Last strategy tried: "
    "{last_strategy}. Last error: {last_error}. "
    "Try rephrasing, switching mode (/mode), or simplifying the request."
)


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

    def _build_repair_summary(self, workflow_run_id: int | None) -> dict | None:
        """Phase 13f: aggregate repair_runs into a Response-friendly summary.
        Returns None when no repair_runs row exists for this workflow_run."""
        if workflow_run_id is None:
            return None
        try:
            repairs = store.repair_runs_for_workflow(workflow_run_id)
        except Exception:
            return None
        if not repairs:
            return None
        last = repairs[-1]
        return {
            "attempts": len(repairs),
            "last_kind": last.get("failure_kind"),
            "last_strategy": last.get("strategy_attempted"),
            "failing_agent": last.get("agent"),
            "last_error": last.get("original_error"),
        }

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
        # Phase 12g: /mode <workflow> overrides routing for the next turn.
        # The pending workflow's persona is honored verbatim (overrides hysteresis).
        if ctx.pending_workflow and ctx.pending_workflow in router.workflow_names():
            workflow_name = ctx.pending_workflow
            chosen = router.workflow_persona(workflow_name)
        else:
            workflow_name = _resolve_workflow_name(chosen, suggested_workflow_name, ctx.auto_mode)
        agents = list(router.workflow_agents(workflow_name))
        mode = router.workflow_mode(workflow_name)
        # Phase 12b: competitive mode requires its own trailing evaluator as
        # part of the mode contract; skip the auto-append to avoid a duplicate.
        if router.workflow_evaluate(workflow_name) and mode != "competitive":
            agents.append("evaluator")
        rounds = router.workflow_rounds(workflow_name)
        timeout_s = router.workflow_timeout_s(workflow_name)
        persona = personas.get(chosen)
        workflow = Workflow(
            persona=chosen,
            model=persona.model,
            skill_name=skill_name,
            execution_mode=mode,
            agents=tuple(agents),
            rounds=rounds,
            timeout_s=timeout_s,
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
        workflow: Workflow,
        workflow_result: WorkflowResult,
        message: str,
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
            decision = governance_decide(
                classification,
                workflow,
                workflow_result,
                message=message,
            )
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
        pending_workflow: str | None = None,
        approved: bool = False,
    ) -> Response:
        """End-to-end orchestration. Returns a Response; caller prints + flushes.

        Phase 13d: the body runs inside a `workflow_buffer()` context. The
        assistant-message commit is staged via `buf.stage(...)` and either
        committed (on result.ok) or dropped (on failure). Audit rows
        (agent_runs, governance_decisions, workflow_runs, notification_queue)
        still write inline — they record what happened, not the result.

        Phase 15: `approved=True` is the re-issue of a turn the user approved
        at the y/n/why prompt — it bypasses the `require_approval` gate.
        """
        with workflow_buffer() as buf:
            return self._handle_with_buffer(
                buf, message, persona_name, auto_mode, pending_skill,
                pending_workflow, approved,
            )

    def _handle_with_buffer(
        self,
        buf,
        message: str,
        persona_name: str,
        auto_mode: bool = False,
        pending_skill: str | None = None,
        pending_workflow: str | None = None,
        approved: bool = False,
    ) -> Response:
        ctx = Context(
            conversation_id=None,
            persona=persona_name,
            auto_mode=auto_mode,
            pending_skill=pending_skill,
            pending_workflow=pending_workflow,
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
            pending_workflow=pending_workflow,
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

        # Phase 10: borderline evaluator confidence -> one Critic + persona retry.
        # Same workflow_run_id so the trace shows the whole story in one row.
        critic_used = False
        ec = result.evaluator_confidence
        critic_low, critic_high = _critic_band()
        if (
            result.ok
            and ec is not None
            and critic_low <= ec < critic_high
            and chosen in personas.VALID_PERSONAS
        ):
            events.dispatch(
                "borderline_confidence",
                {"confidence": ec, "workflow_run_id": workflow_run_id},
            )
            critic_workflow = Workflow(
                persona=chosen,
                model=workflow.model,
                skill_name=workflow.skill_name,
                execution_mode="sequential",
                agents=("critic", chosen),
            )
            retry_result = self.execute(
                critic_workflow, ctx, message, workflow_run_id=workflow_run_id
            )
            if retry_result.ok and retry_result.text:
                # Keep the original evaluator confidence on the final result;
                # the critic-retry workflow does not include the evaluator.
                result = WorkflowResult(
                    text=retry_result.text,
                    ok=True,
                    tokens_in=retry_result.tokens_in,
                    tokens_out=retry_result.tokens_out,
                    model=retry_result.model,
                    latency_ms=retry_result.latency_ms,
                    evaluator_confidence=ec,
                )
                critic_used = True

        # Phase 13f: build repair_summary (if any repair_runs landed) so the
        # Response can surface a y/n retry prompt to the REPL and so the
        # failure apology can interpolate the last failure kind/strategy.
        repair_summary = self._build_repair_summary(workflow_run_id)
        if not result.ok and repair_summary is not None:
            apology = _REPAIR_EXHAUSTED_TEMPLATE.format(
                attempts=repair_summary["attempts"],
                last_kind=repair_summary["last_kind"] or "—",
                last_strategy=repair_summary["last_strategy"] or "—",
                failing_agent=repair_summary["failing_agent"] or "—",
                last_error=repair_summary["last_error"] or "—",
            )
            result = WorkflowResult(
                text=apology,
                ok=False,
                tokens_in=0,
                tokens_out=0,
                model="",
                latency_ms=0,
                evaluator_confidence=result.evaluator_confidence,
            )

        # Phase 10: governance runs before the assistant-message commit so a
        # `reject` decision can override the response text. The rejection is
        # the assistant turn; persist it so /recall and the vault are coherent.
        decision = self.decide(classification, workflow, result, message, ctx)
        # Phase 15: a turn the user already approved at the y/n prompt is
        # re-issued with approved=True — bypass the require_approval gate so
        # the real answer is delivered. The trace records action=auto with
        # reason=approved_by_user.
        if approved and decision.action == Action.REQUIRE_APPROVAL.value:
            decision = replace(
                decision, action=Action.AUTO.value, reason="approved_by_user"
            )
        rejected = decision.action == Action.REJECT.value
        # Phase 14: any gated action (reject / ask_clarification /
        # require_approval) replaces the response with its canned message.
        gate_message = _GATED_MESSAGES.get(decision.action)
        if gate_message is not None:
            result = WorkflowResult(
                text=gate_message,
                ok=True,
                tokens_in=0,
                tokens_out=0,
                model="",
                latency_ms=0,
                evaluator_confidence=result.evaluator_confidence,
            )

        assistant_msg_id = None
        if result.ok:
            mem_started = store.now_iso()
            mem_t0 = time.monotonic()
            # Phase 13d: stage the assistant-message commit instead of
            # executing it directly. The buf.commit() below either runs
            # every staged callable (success) or drops them all (failure).
            buf.stage(
                lambda: default_memory_agent.commit_assistant_turn(
                    conversation_id=conv_id,
                    content=result.text,
                    persona=chosen,
                    model=result.model,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                ),
                description="commit_assistant_turn",
            )
            committed = buf.commit()
            assistant_msg_id = committed[0] if committed else None
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
        else:
            # Workflow failed (e.g., repair exhausted, all agents failed).
            # Nothing was staged for the assistant message; drop the buffer
            # explicitly so the contract is satisfied and the implicit-drop
            # warning doesn't fire.
            buf.drop()
        ts_now = store.now_iso()
        store.upsert_session(
            active_persona=chosen,
            current_conversation_id=conv_id,
            last_message_at=ts_now,
            auto_mode=auto_mode,
        )

        # Phase 13e: distinguish "succeeded thanks to Repair" from a plain
        # first-try success. Light up `repaired` when any repair_runs row
        # reports outcome='recovered' AND the workflow's final result is ok.
        repair_outcome = "success" if result.ok else "failure"
        if result.ok:
            try:
                repairs = store.repair_runs_for_workflow(workflow_run_id)
                if any(r["outcome"] == "recovered" for r in repairs):
                    repair_outcome = "repaired"
            except Exception:
                # Defensive: don't let trace bookkeeping fail the turn.
                pass
        store.update_workflow_run_outcome(
            workflow_run_id,
            outcome=repair_outcome,
            ended_at=ts_now,
        )

        # Phase 14: persist the scored signals the decision matrix produced.
        # `decision.risk` is the governing risk (classifier rating escalated by
        # the keyword backstop); `decision.confidence` is the evaluator score
        # with classifier fallback; `decision.reversibility` is no longer NULL.
        # The fallback-on-error Decision carries None — keep classifier values.
        stored_confidence = (
            result.evaluator_confidence
            if result.evaluator_confidence is not None
            else classification.confidence
        )
        decision_id = store.append_governance_decision(
            workflow_run_id=workflow_run_id,
            intent=classification.intent,
            risk=decision.risk or classification.risk,
            confidence=decision.confidence if decision.confidence is not None else stored_confidence,
            reversibility=decision.reversibility,
            action=decision.action,
        )

        logger.info(
            "master_decision",
            extra={
                "intent": classification.intent,
                "tone": classification.tone,
                "task_type": classification.task_type,
                "risk": decision.risk or classification.risk,
                "reversibility": decision.reversibility,
                "confidence": classification.confidence,
                "evaluator_confidence": result.evaluator_confidence,
                "critic_used": critic_used,
                "persona": chosen,
                "skill": workflow.skill_name,
                "execution_mode": workflow.execution_mode,
                "action": decision.action,
                "decision_reason": decision.reason,
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
        enqueue_source = (
            "rejected" if rejected else ("response" if result.ok else "error")
        )
        token = queue.enqueue_for_delivery(
            text,
            source=enqueue_source,
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
        # Phase 15: when governance held the turn for approval, attach the
        # ApprovalRequest so the REPL can prompt y/n/why off it.
        approval_payload: dict | None = None
        if decision.action == Action.REQUIRE_APPROVAL.value:
            approval_payload = asdict(
                governance_approval.build_request(decision_id, decision, message)
            )

        return Response(
            text=text,
            ok=result.ok,
            persona=chosen,
            skill_name=workflow.skill_name,
            delivery_token=token,
            # Phase 13f: surface y/n retry intent only when Repair gave up.
            requires_user_decision=(not result.ok and repair_summary is not None),
            repair_summary=repair_summary,
            approval=approval_payload,
        )


default_master = MasterAgent()


def handle(
    message: str,
    persona_name: str,
    auto_mode: bool = False,
    pending_skill: str | None = None,
    pending_workflow: str | None = None,
    approved: bool = False,
) -> Response:
    return default_master.handle(
        message, persona_name, auto_mode, pending_skill, pending_workflow, approved
    )
