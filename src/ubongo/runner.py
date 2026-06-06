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
        ctx = store.recall(conv_id, query=current_message)
        summary_text = ctx.summary_text
        # Phase 20: semantically-recalled older turns (outside the recency
        # window) are prepended as a clearly-labelled context block so the model
        # can use them without confusing them for the live conversation flow.
        if ctx.semantic_messages:
            recalled = "\n".join(
                f"- {m.role}: {m.content}" for m in ctx.semantic_messages
            )
            history.append({
                "role": "user",
                "content": f"[Relevant earlier context, retrieved by similarity:]\n{recalled}",
            })
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
            "collaborative": self._run_collaborative,
            "debate": self._run_debate,
            "speculative": self._run_speculative,
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

    # ---------- Phase 13b: multi-strategy recovery helper (sequential) ----------

    def _persist_repair_run(
        self,
        *,
        workflow_run_id: int | None,
        agent_name: str,
        failure_kind: str,
        original_error: str | None,
        strategy_attempted: str,
        peer_agent: str | None,
        override_model: str | None,
        attempt_index: int,
        outcome: str,
        started_at: str,
        ended_at: str | None,
    ) -> None:
        """Phase 13e: persist one repair_runs row. No-op when workflow_run_id
        is None (test paths that bypass master.handle)."""
        if workflow_run_id is None:
            return
        store.append_repair_run(
            workflow_run_id=workflow_run_id,
            agent=agent_name,
            failure_kind=failure_kind,
            original_error=original_error,
            strategy_attempted=strategy_attempted,
            peer_agent=peer_agent,
            override_model=override_model,
            attempt_index=attempt_index,
            outcome=outcome,
            started_at=started_at,
            ended_at=ended_at,
        )

    # ---------- recovery ladder (candidate 01: Repair drives, runner wires) ----------

    async def _run_recovery(
        self,
        *,
        agent=None,
        agent_name: str,
        original_result: AgentResult,
        scope: str,
        message: str,
        history: list,
        summary_text: str | None,
        prior_findings: list[str],
        workflow: "Workflow",
        context: "Context",
        workflow_run_id: int | None,
    ) -> "RecoveryOutcome":
        """Wire Repair's recover() to the runner's dispatch + persist seams.

        Carries no recovery taxonomy: it only knows how to run one attempt
        (dispatch a peer, or re-dispatch the original agent with the plan's
        overrides) and how to persist one repair_runs row. Repair owns the
        ladder, the Strategy enum, and the give-up decision. `scope` is a plain
        string — "ladder" (sequential full ladder) or "peer_only" (single
        fan-out hop) — kept stringly so the runner module never imports the
        repair taxonomy at load time (that would eagerly build the repair
        singleton and validate config before __main__'s error guard runs).
        """
        from ubongo.agents.repair import (
            RecoveryOutcome,
            RecoveryScope,
            RepairAttempt,
        )

        if scope == "ladder":
            allow = RecoveryScope.LADDER
        elif scope == "peer_only":
            allow = RecoveryScope.PEER_ONLY
        else:
            raise ValueError(f"_run_recovery: unknown scope {scope!r}")

        repair = self.registry.get("repair")
        if repair is None or not hasattr(repair, "recover"):
            return RecoveryOutcome(result=original_result)

        async def dispatch(plan):
            if plan.peer_agent:
                peer = self.registry.get(plan.peer_agent)
                if peer is None:
                    logger.warning(
                        "repair_peer_not_registered",
                        extra={"agent": agent_name, "peer": plan.peer_agent},
                    )
                    return None
                return await self._dispatch_agent_async(
                    agent=peer,
                    agent_name=plan.peer_agent,
                    message=message,
                    history=history,
                    summary_text=summary_text,
                    prior_findings=prior_findings,
                    workflow=workflow,
                    context=context,
                    workflow_run_id=workflow_run_id,
                    override_model=None,
                    retried=True,
                )
            extra_metadata: dict = {}
            if plan.prompt_hint:
                extra_metadata["repair_prompt_hint"] = plan.prompt_hint
            if plan.max_tokens_cap:
                extra_metadata["max_tokens_override"] = plan.max_tokens_cap
            return await self._dispatch_agent_async(
                agent=agent,
                agent_name=agent_name,
                message=message,
                history=history,
                summary_text=summary_text,
                prior_findings=prior_findings,
                workflow=workflow,
                context=context,
                workflow_run_id=workflow_run_id,
                override_model=plan.override_model,
                retried=True,
                extra_metadata=extra_metadata or None,
            )

        def persist(attempt: RepairAttempt) -> None:
            self._persist_repair_run(
                workflow_run_id=workflow_run_id,
                agent_name=agent_name,
                failure_kind=attempt.failure_kind,
                original_error=attempt.original_error,
                strategy_attempted=attempt.strategy_attempted,
                peer_agent=attempt.peer_agent,
                override_model=attempt.override_model,
                attempt_index=attempt.attempt_index,
                outcome=attempt.outcome,
                started_at=attempt.started_at,
                ended_at=attempt.ended_at,
            )

        return await repair.recover(
            agent_name=agent_name,
            original=original_result,
            allow=allow,
            dispatch=dispatch,
            persist=persist,
            clock=store.now_iso,
        )

    # ---------- sequential mode (Phase 9 baseline + Phase 13 Repair ladder) ----------

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

            # Phase 13b: on failure, walk the Repair strategy ladder. Repair
            # drives the full ladder; the runner only wires dispatch + persist.
            if not result.ok and repair is not None and hasattr(repair, "recover"):
                result = (await self._run_recovery(
                    agent=agent,
                    agent_name=agent_name,
                    original_result=result,
                    scope="ladder",
                    message=message,
                    history=history,
                    summary_text=summary_text,
                    prior_findings=prior_findings,
                    workflow=workflow,
                    context=context,
                    workflow_run_id=workflow_run_id,
                )).result

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
        - Phase 13c: per-producer peer replacement via _run_recovery with
          scope=PEER_ONLY (the only Repair strategy fan-out modes use;
          cancel-and-retry semantics in asyncio.gather are still ambiguous).
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

        # Phase 13c: per-failure peer replacement. Single hop only.
        for i, (name, result) in enumerate(zip(agent_names, results)):
            if result.ok:
                continue
            outcome = await self._run_recovery(
                agent_name=name,
                original_result=result,
                scope="peer_only",
                message=message,
                history=history,
                summary_text=summary_text,
                prior_findings=[],
                workflow=workflow,
                context=context,
                workflow_run_id=workflow_run_id,
            )
            if outcome.peer_agent is not None and outcome.result.ok:
                results[i] = outcome.result
                peer_agent = self.registry.get(outcome.peer_agent)
                if peer_agent is not None:
                    agents[i] = peer_agent

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

        # Phase 13c: per-failure peer replacement before ranking, so a
        # recovered candidate still competes. Single hop only (fan-out rule).
        for i, (name, result) in enumerate(zip(competitor_names, results)):
            if result.ok:
                continue
            outcome = await self._run_recovery(
                agent_name=name,
                original_result=result,
                scope="peer_only",
                message=message,
                history=history,
                summary_text=summary_text,
                prior_findings=[],
                workflow=workflow,
                context=context,
                workflow_run_id=workflow_run_id,
            )
            if outcome.peer_agent is not None and outcome.result.ok:
                results[i] = outcome.result

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


    # ---------- collaborative mode (Phase 12c) ----------

    async def _run_collaborative(
        self,
        *,
        workflow: "Workflow",
        context: "Context",
        message: str,
        summary_text: str | None,
        history: list,
        workflow_run_id: int | None,
    ) -> "WorkflowResult":
        """All producers run in parallel with role-driven specialization;
        outputs are merged structurally under "## <role>" headings.

        - workflow.agents producers run in parallel; if 'evaluator' is the
          LAST entry (Phase-10 auto-append from evaluate=true), it runs
          SEQUENTIALLY after the merge to score the merged document.
        - prior_findings is empty for each producer; they specialize via
          their existing system prompts (research = facts, critic = risks).
        - WorkflowResult.text = merged document; ok=any producer ok.
        """
        from ubongo.master import WorkflowResult as _WR

        evaluator_name: str | None = None
        producer_names: list[str] = []
        producer_agents: list = []
        # Strip trailing evaluator if present (auto-appended by master.plan
        # via the Phase-10 evaluate flag) so it can run sequentially after
        # the merge instead of in parallel with the producers.
        agents_list = list(workflow.agents)
        if agents_list and agents_list[-1] == "evaluator":
            evaluator_name = "evaluator"
            agents_list = agents_list[:-1]

        for name in agents_list:
            if name == "repair":
                continue
            agent = self.registry.get(name)
            if agent is None:
                logger.warning("agent_not_registered", extra={"agent": name})
                continue
            producer_names.append(name)
            producer_agents.append(agent)

        if not producer_agents:
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
            for name, agent in zip(producer_names, producer_agents)
        ]
        results: list[AgentResult] = await asyncio.gather(*tasks)

        # Phase 13c: for each failed producer, ask Repair if peer-replacement
        # applies. If so, the peer's result takes that slot (with the peer's
        # role heading); /trace shows the peer's agent_runs row under its
        # real name. The smoke 12.4 fix routes critic_no_candidate to
        # architect, restoring the merged-doc's critic section.
        for i, (name, agent, result) in enumerate(zip(producer_names, producer_agents, results)):
            if result.ok:
                continue
            outcome = await self._run_recovery(
                agent_name=name,
                original_result=result,
                scope="peer_only",
                message=message,
                history=history,
                summary_text=summary_text,
                prior_findings=[],
                workflow=workflow,
                context=context,
                workflow_run_id=workflow_run_id,
            )
            if outcome.peer_agent is not None and outcome.result.ok:
                results[i] = outcome.result
                peer_agent = self.registry.get(outcome.peer_agent)
                if peer_agent is not None:
                    producer_agents[i] = peer_agent

        # Structural merge: one section per ok producer, ordered by
        # workflow.agents (deterministic).
        sections: list[str] = []
        any_failure = False
        any_ok = False
        for name, agent, result in zip(producer_names, producer_agents, results):
            if not result.ok:
                any_failure = True
                continue
            any_ok = True
            heading = getattr(agent, "role", name)
            sections.append(f"## {heading}\n\n{result.text}")
        if not sections:
            return _WR(
                text=LLM_FAILURE_MESSAGE, ok=False,
                tokens_in=0, tokens_out=0, model="",
                latency_ms=0, evaluator_confidence=None,
            )

        merged_text = "\n\n".join(sections)
        total_tokens_in = sum(r.tokens_in for r in results if r.ok)
        total_tokens_out = sum(r.tokens_out for r in results if r.ok)
        max_latency = max((r.latency_ms for r in results if r.ok), default=0)

        # Sequential post-step: evaluator scores the merged document if present.
        evaluator_confidence: float | None = None
        if evaluator_name is not None:
            evaluator = self.registry.get(evaluator_name)
            if evaluator is not None:
                # Synthesize an AgentInput where prior_findings = [merged_text]
                # so EvaluatorAgent.run picks it up via its existing
                # "candidate = prior_findings[-1]" path.
                eval_result = await self._dispatch_agent_async(
                    agent=evaluator,
                    agent_name=evaluator_name,
                    message=message,
                    history=history,
                    summary_text=summary_text,
                    prior_findings=[merged_text],
                    workflow=workflow,
                    context=context,
                    workflow_run_id=workflow_run_id,
                    override_model=None,
                    retried=False,
                )
                if eval_result.ok and eval_result.confidence is not None:
                    evaluator_confidence = eval_result.confidence

        return _WR(
            text=merged_text,
            ok=any_ok,
            tokens_in=total_tokens_in,
            tokens_out=total_tokens_out,
            model="collaborative",
            latency_ms=max_latency,
            evaluator_confidence=evaluator_confidence,
        )


    # ---------- debate mode (Phase 12d) ----------

    async def _run_debate(
        self,
        *,
        workflow: "Workflow",
        context: "Context",
        message: str,
        summary_text: str | None,
        history: list,
        workflow_run_id: int | None,
    ) -> "WorkflowResult":
        """Two debaters argue N rounds; synthesizer summarizes.

        Convention: workflow.agents = [debater_a, debater_b, ..., synthesizer].
        First two entries are the debaters; the LAST entry is the synthesizer.
        workflow.rounds (default 2) = number of full rounds (both debaters speak
        once per round). Each debater sees the full prior transcript via
        prior_findings; the synthesizer sees the full transcript and produces
        the final response.
        """
        if len(workflow.agents) < 3:
            raise ValueError(
                "debate workflows need at least 3 entries: [debater_a, debater_b, synthesizer]"
            )
        debater_a_name = workflow.agents[0]
        debater_b_name = workflow.agents[1]
        synthesizer_name = workflow.agents[-1]
        rounds = workflow.rounds if workflow.rounds is not None else 2

        for name in (debater_a_name, debater_b_name, synthesizer_name):
            if name not in self.registry:
                raise ValueError(f"debate workflow references unknown agent {name!r}")

        transcript: list[tuple[str, str]] = []  # [(speaker, text), ...]
        any_failure = False
        # Phase 13c: a debater recovered by peer replacement keeps that peer
        # for the rest of the turn (original speaker name -> peer name).
        substitutions: dict[str, str] = {}

        for round_no in range(rounds):
            for speaker in (debater_a_name, debater_b_name):
                actual = substitutions.get(speaker, speaker)
                agent = self.registry[actual]
                prior = [f"## Round {i // 2 + 1} — {sp}\n\n{txt}"
                         for i, (sp, txt) in enumerate(transcript)]
                debate_role = "challenge" if transcript else None
                result = await self._dispatch_agent_async(
                    agent=agent,
                    agent_name=actual,
                    message=message,
                    history=history,
                    summary_text=summary_text,
                    prior_findings=prior,
                    workflow=workflow,
                    context=context,
                    workflow_run_id=workflow_run_id,
                    override_model=None,
                    retried=False,
                    extra_metadata=({"debate_role": debate_role} if debate_role else None),
                )
                if not result.ok:
                    # Phase 13c: one peer substitution before short-circuiting,
                    # so a failed debater does not drop a voice from synthesis.
                    outcome = await self._run_recovery(
                        agent_name=actual,
                        original_result=result,
                        scope="peer_only",
                        message=message,
                        history=history,
                        summary_text=summary_text,
                        prior_findings=prior,
                        workflow=workflow,
                        context=context,
                        workflow_run_id=workflow_run_id,
                    )
                    if outcome.peer_agent is not None and outcome.result.ok:
                        substitutions[speaker] = outcome.peer_agent
                        transcript.append((outcome.peer_agent, outcome.result.text))
                        continue
                    any_failure = True
                    logger.warning(
                        "debate_short_circuit",
                        extra={"speaker": actual, "round": round_no + 1},
                    )
                    break
                transcript.append((actual, result.text))
            else:
                continue
            break  # debate short-circuited; jump to synthesis with whatever exists

        synth_agent = self.registry[synthesizer_name]
        synth_prior = [f"## {sp}\n\n{txt}" for sp, txt in transcript]
        synth_result = await self._dispatch_agent_async(
            agent=synth_agent,
            agent_name=synthesizer_name,
            message=message,
            history=history,
            summary_text=summary_text,
            prior_findings=synth_prior,
            workflow=workflow,
            context=context,
            workflow_run_id=workflow_run_id,
            override_model=None,
            retried=False,
            extra_metadata={"debate_role": "synthesize"},
        )

        if not synth_result.ok:
            return self._build_workflow_result(None, None, None, True)

        last_composer = synth_result if getattr(synth_agent, "composer", False) else None
        return self._build_workflow_result(
            last_composer, synth_result,
            synth_result.confidence if synth_result.confidence is not None else None,
            any_failure,
        )


    # ---------- speculative mode (Phase 12e) ----------

    async def _run_speculative(
        self,
        *,
        workflow: "Workflow",
        context: "Context",
        message: str,
        summary_text: str | None,
        history: list,
        workflow_run_id: int | None,
    ) -> "WorkflowResult":
        """Cheap-first response; strong validates; correction concat on disagreement.

        Convention: workflow.agents = [cheap, strong, evaluator?]. Cheap and
        strong run concurrently with a hard total timeout (workflow.timeout_s,
        default 10s). The leader text is whichever cheap result is ok (cheap
        is the speculative payoff); if cheap failed, strong's text is used.
        If both ran ok AND an evaluator is present AND evaluator.agree returns
        False, a correction block is appended to the cheap response.

        v0.1 limitation: in-turn (within a single master.handle call). True
        background (cheap returns instantly while strong runs after the user
        sees the response) waits for cross-turn pending-tasks infra.
        """
        from ubongo.master import WorkflowResult as _WR

        if len(workflow.agents) < 2:
            raise ValueError(
                "speculative workflows need at least [cheap, strong] in agents"
            )
        cheap_name = workflow.agents[0]
        strong_name = workflow.agents[1]
        evaluator_name: str | None = (
            workflow.agents[-1] if (
                len(workflow.agents) >= 3 and workflow.agents[-1] == "evaluator"
            ) else None
        )
        timeout_s = workflow.timeout_s if workflow.timeout_s is not None else 10
        for name in (cheap_name, strong_name):
            if name not in self.registry:
                raise ValueError(
                    f"speculative workflow references unknown agent {name!r}"
                )

        cheap_agent = self.registry[cheap_name]
        strong_agent = self.registry[strong_name]

        cheap_task = asyncio.create_task(self._dispatch_agent_async(
            agent=cheap_agent, agent_name=cheap_name,
            message=message, history=history, summary_text=summary_text,
            prior_findings=[], workflow=workflow, context=context,
            workflow_run_id=workflow_run_id,
            override_model=None, retried=False,
        ))
        strong_task = asyncio.create_task(self._dispatch_agent_async(
            agent=strong_agent, agent_name=strong_name,
            message=message, history=history, summary_text=summary_text,
            prior_findings=[], workflow=workflow, context=context,
            workflow_run_id=workflow_run_id,
            override_model=None, retried=False,
        ))

        try:
            await asyncio.wait_for(
                asyncio.gather(cheap_task, strong_task, return_exceptions=True),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning("speculative_timeout", extra={"timeout_s": timeout_s})

        cheap_result = cheap_task.result() if cheap_task.done() and not cheap_task.cancelled() else None
        strong_result = strong_task.result() if strong_task.done() and not strong_task.cancelled() else None

        # Phase 13c: cheap is the speculative leader. If it ran but failed,
        # attempt one peer substitution before falling back to strong. A
        # timed-out cheap (no AgentResult) skips replacement — strong is the
        # natural fallback there. A failed strong while cheap is ok is left
        # alone: the successful leader already satisfies the turn.
        if cheap_result is not None and not cheap_result.ok:
            outcome = await self._run_recovery(
                agent_name=cheap_name,
                original_result=cheap_result,
                scope="peer_only",
                message=message,
                history=history,
                summary_text=summary_text,
                prior_findings=[],
                workflow=workflow,
                context=context,
                workflow_run_id=workflow_run_id,
            )
            if outcome.peer_agent is not None and outcome.result.ok:
                cheap_result = outcome.result

        # Pick base: prefer cheap (speculative payoff). Fall back to strong.
        if cheap_result and cheap_result.ok:
            base = cheap_result
        elif strong_result and strong_result.ok:
            base = strong_result
        else:
            return self._build_workflow_result(None, None, None, True)

        text = base.text
        evaluator_confidence: float | None = None

        # Validation: BOTH ok, base IS cheap, evaluator present.
        if (
            evaluator_name is not None
            and cheap_result and cheap_result.ok
            and strong_result and strong_result.ok
            and base is cheap_result
        ):
            evaluator = self.registry.get(evaluator_name)
            if evaluator is not None and hasattr(evaluator, "agree"):
                agree_started = store.now_iso()
                agree = await asyncio.to_thread(
                    evaluator.agree, message, cheap_result.text, strong_result.text,
                )
                agree_ended = store.now_iso()
                if workflow_run_id is not None:
                    store.append_agent_run(
                        workflow_run_id=workflow_run_id,
                        agent="evaluator",
                        model=getattr(evaluator, "default_model", None),
                        input={"cheap_len": len(cheap_result.text),
                               "strong_len": len(strong_result.text)},
                        output={"agree": agree},
                        confidence=None,
                        tokens_in=0, tokens_out=0, latency_ms=0,
                        outcome="success" if agree is not None else "failure",
                        started_at=agree_started, ended_at=agree_ended,
                        retried=False,
                    )
                if agree is False:
                    text = (
                        f"{cheap_result.text}\n\n---\n\n"
                        f"[Correction (slower model):]\n\n{strong_result.text}"
                    )

        return _WR(
            text=text,
            ok=base.ok,
            tokens_in=base.tokens_in,
            tokens_out=base.tokens_out,
            model=base.model or "",
            latency_ms=base.latency_ms,
            evaluator_confidence=evaluator_confidence,
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
