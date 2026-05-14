"""Workflow runner: dispatches the agents listed in workflow.agents.

Phase 12: the runner is async-internally, sync-externally. WorkflowRunner.execute
keeps its sync signature so master.handle / repl.run / oneshot.run stay sync;
internally it bridges to _execute_async via asyncio.run. Each execution mode
is a strategy coroutine (_run_sequential, _run_parallel, ...) selected off
workflow.execution_mode. Agents themselves stay sync; the runner wraps each
agent.run call in asyncio.to_thread when fanning out.

Mode coverage:
- sequential   : Phase 9 baseline; threads prior_findings forward; Repair retry.
- parallel     : Phase 12a; asyncio.gather; agents see no prior_findings;
                 no Repair retry (cancel-and-retry semantics in fan-out are
                 ambiguous; Phase 13 may revisit).
- competitive  : Phase 12b; planned.
- collaborative: Phase 12c; planned.
- debate       : Phase 12d; planned.
- speculative  : Phase 12e; planned.
"""

from __future__ import annotations

import asyncio
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

KNOWN_MODES: tuple[str, ...] = (
    "sequential",
    "parallel",
    "competitive",
    "collaborative",
    "debate",
    "speculative",
)


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
        if not history or history[-1].get("content") != current_message or history[-1].get("role") != "user":
            history.append({"role": "user", "content": current_message})
    else:
        history.append({"role": "user", "content": current_message})
    return summary_text, history


class WorkflowRunner:
    def __init__(self, registry: dict[str, Agent]):
        self.registry = registry

    # ---------- agent dispatch (async; sync agents wrapped via asyncio.to_thread) ----------

    async def _dispatch_agent_async(
        self,
        *,
        agent,
        agent_name: str,
        message: str,
        history: list,
        summary_text: str | None,
        prior_findings: list[str],
        workflow,
        context,
        workflow_run_id: int | None,
        override_model: str | None,
        retried: bool,
        extra_metadata: dict | None = None,
    ) -> AgentResult:
        """Run a single agent off-thread, record the agent_runs row, dispatch
        lifecycle events. Used by every mode strategy; the only place we cross
        the sync/async boundary into agent code."""
        metadata: dict = {
            "persona": workflow.persona,
            "skill": workflow.skill_name,
        }
        if override_model:
            metadata["override_model"] = override_model
        if extra_metadata:
            metadata.update(extra_metadata)

        input = AgentInput(
            message=message,
            history=tuple(history),
            summary_text=summary_text,
            prior_findings=tuple(prior_findings),
            metadata=metadata,
        )
        events.dispatch(
            "agent_started",
            {"agent": agent_name, "input_message_len": len(message), "retried": retried},
        )
        started_at = store.now_iso()
        t0 = time.monotonic()
        try:
            result = await asyncio.to_thread(agent.run, input, context)
        except Exception as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            logger.warning(
                "agent_exception",
                extra={"agent": agent_name, "cause": str(exc), "retried": retried},
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
                retried=retried,
            )

        if result.ok:
            events.dispatch(
                "agent_completed",
                {
                    "agent": agent_name,
                    "ok": True,
                    "tokens_in": result.tokens_in,
                    "tokens_out": result.tokens_out,
                    "retried": retried,
                },
            )
        else:
            events.dispatch(
                "agent_failed",
                {"agent": agent_name, "error": result.error, "retried": retried},
            )
        return result

    # ---------- public sync entry; bridges to _execute_async ----------

    def execute(
        self,
        workflow: "Workflow",
        context: "Context",
        message: str,
        workflow_run_id: int | None = None,
    ) -> "WorkflowResult":
        """Sync entry point. Bridges to the async strategy via asyncio.run.

        master.handle / repl.run / oneshot.run remain sync; concurrency lives
        inside the strategy methods where it pays off (parallel, competitive,
        collaborative, debate, speculative).
        """
        return asyncio.run(
            self._execute_async(workflow, context, message, workflow_run_id)
        )

    async def _execute_async(
        self,
        workflow: "Workflow",
        context: "Context",
        message: str,
        workflow_run_id: int | None,
    ) -> "WorkflowResult":
        from ubongo.master import WorkflowResult as _WR

        strategies = {
            "sequential": self._run_sequential,
            "parallel": self._run_parallel,
            "competitive": self._run_competitive,
        }
        strategy = strategies.get(workflow.execution_mode)
        if strategy is None:
            known = ", ".join(sorted(strategies))
            raise NotImplementedError(
                f"Phase 12: unknown mode {workflow.execution_mode!r}. Known: {known}."
            )

        summary_text, history = build_message_history(context.conversation_id, message)
        return await strategy(
            workflow=workflow,
            context=context,
            message=message,
            summary_text=summary_text,
            history=history,
            workflow_run_id=workflow_run_id,
        )

    # ---------- shared composer + result selection ----------

    def _build_workflow_result(
        self,
        last_composer_result: AgentResult | None,
        last_ok_result: AgentResult | None,
        evaluator_confidence: float | None,
        any_failure: bool,
    ) -> "WorkflowResult":
        from ubongo.master import WorkflowResult as _WR

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
        return _WR(
            text=text_source.text,
            ok=not any_failure or text_source.ok,
            tokens_in=text_source.tokens_in,
            tokens_out=text_source.tokens_out,
            model=text_source.model or "",
            latency_ms=text_source.latency_ms,
            evaluator_confidence=evaluator_confidence,
        )

    # ---------- sequential mode (Phase 9 baseline + Phase 11 Repair) ----------

    async def _run_sequential(
        self,
        *,
        workflow: "Workflow",
        context: "Context",
        message: str,
        summary_text: str | None,
        history: list,
        workflow_run_id: int | None,
    ) -> "WorkflowResult":
        prior_findings: list[str] = []
        last_ok_result: AgentResult | None = None
        last_composer_result: AgentResult | None = None
        evaluator_confidence: float | None = None
        any_failure = False
        retried_agents: set[str] = set()

        repair = self.registry.get("repair")

        for agent_name in workflow.agents:
            if agent_name == "repair":
                continue

            agent = self.registry.get(agent_name)
            if agent is None:
                logger.warning("agent_not_registered", extra={"agent": agent_name})
                any_failure = True
                continue

            result = await self._dispatch_agent_async(
                agent=agent,
                agent_name=agent_name,
                message=message,
                history=history,
                summary_text=summary_text,
                prior_findings=prior_findings,
                workflow=workflow,
                context=context,
                workflow_run_id=workflow_run_id,
                override_model=None,
                retried=False,
            )

            # Phase 11d: on failure, ask Repair for a single retry (sequential only).
            if (
                not result.ok
                and repair is not None
                and agent_name not in retried_agents
                and hasattr(repair, "plan_retry")
            ):
                input_for_plan = AgentInput(
                    message=message,
                    history=tuple(history),
                    summary_text=summary_text,
                    prior_findings=tuple(prior_findings),
                    metadata={"persona": workflow.persona, "skill": workflow.skill_name},
                )
                plan = repair.plan_retry(agent_name, result, input_for_plan)
                if plan is not None:
                    retried_agents.add(agent_name)
                    logger.info(
                        "agent_retry",
                        extra={"agent": agent_name, "model": plan.get("model")},
                    )
                    result = await self._dispatch_agent_async(
                        agent=agent,
                        agent_name=agent_name,
                        message=message,
                        history=history,
                        summary_text=summary_text,
                        prior_findings=prior_findings,
                        workflow=workflow,
                        context=context,
                        workflow_run_id=workflow_run_id,
                        override_model=plan.get("model"),
                        retried=True,
                    )

            if result.ok:
                if result.text:
                    prior_findings.append(result.text)
                last_ok_result = result
                if getattr(agent, "composer", False):
                    last_composer_result = result
                if result.confidence is not None:
                    evaluator_confidence = result.confidence
            else:
                any_failure = True

        return self._build_workflow_result(
            last_composer_result, last_ok_result, evaluator_confidence, any_failure
        )

    # ---------- parallel mode (Phase 12a) ----------

    async def _run_parallel(
        self,
        *,
        workflow: "Workflow",
        context: "Context",
        message: str,
        summary_text: str | None,
        history: list,
        workflow_run_id: int | None,
    ) -> "WorkflowResult":
        """Fan out every agent in workflow.agents via asyncio.gather.

        - Every agent sees prior_findings == () (no inter-agent threading).
        - Repair retry does not fire in parallel mode (cancel-and-retry semantics
          are ambiguous; Phase 13 may revisit).
        - last-composer-wins still holds, picked by INDEX in workflow.agents
          (deterministic, even though tasks finish in any order).
        - WorkflowResult.ok requires every agent to succeed.
        """
        agent_names: list[str] = []
        agents: list = []
        for name in workflow.agents:
            if name == "repair":
                continue
            agent = self.registry.get(name)
            if agent is None:
                logger.warning("agent_not_registered", extra={"agent": name})
                continue
            agent_names.append(name)
            agents.append(agent)

        if not agents:
            return self._build_workflow_result(None, None, None, True)

        tasks = [
            self._dispatch_agent_async(
                agent=agent,
                agent_name=name,
                message=message,
                history=history,
                summary_text=summary_text,
                prior_findings=[],
                workflow=workflow,
                context=context,
                workflow_run_id=workflow_run_id,
                override_model=None,
                retried=False,
            )
            for name, agent in zip(agent_names, agents)
        ]
        results: list[AgentResult] = await asyncio.gather(*tasks)

        last_ok_result: AgentResult | None = None
        last_composer_result: AgentResult | None = None
        evaluator_confidence: float | None = None
        any_failure = False
        for name, agent, result in zip(agent_names, agents, results):
            if result.ok:
                last_ok_result = result
                if getattr(agent, "composer", False):
                    last_composer_result = result
                if result.confidence is not None:
                    evaluator_confidence = result.confidence
            else:
                any_failure = True

        return self._build_workflow_result(
            last_composer_result, last_ok_result, evaluator_confidence, any_failure
        )


    # ---------- competitive mode (Phase 12b) ----------

    async def _run_competitive(
        self,
        *,
        workflow: "Workflow",
        context: "Context",
        message: str,
        summary_text: str | None,
        history: list,
        workflow_run_id: int | None,
    ) -> "WorkflowResult":
        """N candidate agents run in parallel; Evaluator picks a winner.

        Convention: workflow.agents[:-1] are competitors; workflow.agents[-1]
        MUST be 'evaluator'. Runner validates at execute time.
        - Repair retry NOT consulted in competitive mode (same fan-out reason
          as parallel; Phase 13 may revisit).
        - WorkflowResult.text = winning candidate's text.
        - WorkflowResult.evaluator_confidence = winner's score (so it still
          feeds governance via the existing Phase 10 path).
        - On rank() failure (parse error / LLM error): fall back to the FIRST
          ok candidate; if none ok, WorkflowResult.ok=False.
        """
        from ubongo.master import WorkflowResult as _WR

        if not workflow.agents or workflow.agents[-1] != "evaluator":
            raise ValueError(
                "competitive workflows must end with 'evaluator'; got "
                f"{list(workflow.agents)!r}"
            )
        evaluator = self.registry.get("evaluator")
        if evaluator is None or not hasattr(evaluator, "rank"):
            raise ValueError("competitive mode requires an EvaluatorAgent with rank()")

        competitor_names: list[str] = []
        competitor_agents: list = []
        for name in workflow.agents[:-1]:
            if name == "repair":
                continue
            agent = self.registry.get(name)
            if agent is None:
                logger.warning("agent_not_registered", extra={"agent": name})
                continue
            competitor_names.append(name)
            competitor_agents.append(agent)

        if not competitor_agents:
            return self._build_workflow_result(None, None, None, True)

        tasks = [
            self._dispatch_agent_async(
                agent=agent,
                agent_name=name,
                message=message,
                history=history,
                summary_text=summary_text,
                prior_findings=[],
                workflow=workflow,
                context=context,
                workflow_run_id=workflow_run_id,
                override_model=None,
                retried=False,
            )
            for name, agent in zip(competitor_names, competitor_agents)
        ]
        results: list[AgentResult] = await asyncio.gather(*tasks)

        ok_pairs = [(n, r) for n, r in zip(competitor_names, results) if r.ok]
        if not ok_pairs:
            return self._build_workflow_result(None, None, None, True)

        # Evaluator.rank is a sync LLM call; off-thread it.
        rank_started = store.now_iso()
        ranking = await asyncio.to_thread(
            evaluator.rank,
            message,
            [(n, r.text) for n, r in ok_pairs],
        )
        rank_ended = store.now_iso()

        # Pick winner: rank result if present, else first ok candidate.
        if ranking is None:
            logger.warning("competitive_rank_fallback", extra={"reason": "rank_returned_none"})
            winner_name, winner_result = ok_pairs[0]
            winner_score = None
        else:
            winner_name = ranking["winner"]
            # Find the winner's AgentResult by name; ranking guarantees it's in ok_pairs.
            winner_result = next(r for n, r in ok_pairs if n == winner_name)
            winner_scores = ranking.get("scores") or []
            winner_score = next(
                (s["score"] for s in winner_scores if s.get("index") == ranking["winner_index"]),
                None,
            )

        # Persist the evaluator's ranking as an agent_runs row so /trace shows
        # the judging step. confidence carries the winner's score (or None).
        if workflow_run_id is not None:
            store.append_agent_run(
                workflow_run_id=workflow_run_id,
                agent="evaluator",
                model=getattr(evaluator, "default_model", None),
                input={"candidates": len(ok_pairs), "message_len": len(message)},
                output={"winner": winner_name, "ranking": ranking},
                confidence=winner_score,
                tokens_in=0,
                tokens_out=0,
                latency_ms=0,  # off-thread; the wrapping rank() call already logs its own latency
                outcome="success" if ranking is not None else "failure",
                started_at=rank_started,
                ended_at=rank_ended,
                retried=False,
            )

        return _WR(
            text=winner_result.text,
            ok=True,
            tokens_in=winner_result.tokens_in,
            tokens_out=winner_result.tokens_out,
            model=winner_result.model or "",
            latency_ms=winner_result.latency_ms,
            evaluator_confidence=winner_score,
        )


def default_registry() -> dict[str, Agent]:
    """Build the agent registry. Imported lazily to avoid circular imports
    (master/runner/agents form a cycle through Context type-hints).

    Phase 10: Persona Agents use bare registry names (architect, operator,
    casual) instead of the Phase-9 `persona:<name>` prefix. Evaluator and
    Critic land here too.
    Phase 11: Coding, Execution, Repair workers added.
    """
    from ubongo.agents.coding import CodingAgent
    from ubongo.agents.critic import CriticAgent
    from ubongo.agents.evaluator import EvaluatorAgent
    from ubongo.agents.execution import ExecutionAgent
    from ubongo.agents.memory import default_memory_agent
    from ubongo.agents.personas import (
        ArchitectPersona,
        CasualPersona,
        OperatorPersona,
    )
    from ubongo.agents.repair import default_repair_agent
    from ubongo.agents.research import ResearchAgent

    return {
        "research": ResearchAgent(),
        "memory": default_memory_agent,
        "evaluator": EvaluatorAgent(),
        "critic": CriticAgent(),
        "coding": CodingAgent(),
        "execution": ExecutionAgent(),
        "repair": default_repair_agent,
        "architect": ArchitectPersona(),
        "operator": OperatorPersona(),
        "casual": CasualPersona(),
    }
