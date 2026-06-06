"""The one agent-invocation core shared by the runner's modes and the eval
sandbox (candidate 02 of the 2026-06-05 architecture review).

What duplicated and silently drifted across the six runner modes and the
sandbox's `_run_workflow_isolated` was the *decision logic* of running a list of
agents: resolving names to agents (skip ``repair``, drop missing) and the
sequential harvest (thread prior findings, pick the last composer, carry the
last confidence, track failure and tokens).

*Dispatch* legitimately differs and is NOT here: the runner dispatches async with
side effects (events + agent_runs persistence + the repair ladder); the sandbox
dispatches sync and bare. The sandbox also runs synchronously on the GP loop's
event-loop thread, so this core is sync and dispatch-agnostic — each caller drives
the loop and calls :meth:`SequentialHarvest.observe` after each dispatch. This
module imports no store, events, or registry mutation: it is side-effect-free
(ADR-0007).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ubongo.agents.base import Agent, AgentResult

logger = logging.getLogger("ubongo.invoke")


def resolve_agents(
    registry: dict[str, Agent], names
) -> list[tuple[str, Agent]]:
    """Resolve workflow agent names to (name, agent) pairs.

    Skips the ``repair`` agent (it is consulted via recover(), never run as a
    workflow step) and drops names not in the registry with a warning. This is
    the resolution loop all six modes and the sandbox repeated.
    """
    resolved: list[tuple[str, Agent]] = []
    for name in names:
        if name == "repair":
            continue
        agent = registry.get(name)
        if agent is None:
            logger.warning("agent_not_registered", extra={"agent": name})
            continue
        resolved.append((name, agent))
    return resolved


@dataclass(frozen=True)
class InvokeOutcome:
    """The harvest of a sequential invoke: what every consumer reads back."""

    last_ok: AgentResult | None
    last_composer: AgentResult | None
    evaluator_confidence: float | None
    any_failure: bool
    total_tokens: int
    prior_findings: tuple[str, ...]

    @property
    def composer_text(self) -> str:
        """The composer's text, falling back to the last threaded finding.

        Mirrors the sandbox's ``composer_text or (prior[-1] if prior else "")``:
        a composer that produced no text falls through to the last finding.
        """
        if self.last_composer is not None and self.last_composer.text:
            return self.last_composer.text
        return self.prior_findings[-1] if self.prior_findings else ""


class SequentialHarvest:
    """Stateful, sync harvester for a sequential agent run.

    The caller drives the loop and dispatches each agent (async or sync, with or
    without side effects); after each dispatch it calls :meth:`observe`. Read
    :attr:`prior` to feed the next dispatch, and :meth:`outcome` at the end. The
    threading / composer / confidence / failure / token semantics live here, so
    the runner's sequential mode and the sandbox cannot drift apart.
    """

    def __init__(self, *, thread_prior: bool = True) -> None:
        self._thread_prior = thread_prior
        self._prior: list[str] = []
        self.last_ok: AgentResult | None = None
        self.last_composer: AgentResult | None = None
        self.evaluator_confidence: float | None = None
        self.any_failure: bool = False
        self.total_tokens: int = 0

    @property
    def prior(self) -> tuple[str, ...]:
        """The findings threaded so far — pass into the next dispatch."""
        return tuple(self._prior)

    def observe(self, agent: Agent, result: AgentResult) -> None:
        """Fold one dispatched result into the harvest."""
        self.total_tokens += (result.tokens_in or 0) + (result.tokens_out or 0)
        if result.ok:
            if result.text and self._thread_prior:
                self._prior.append(result.text)
            self.last_ok = result
            if getattr(agent, "composer", False):
                self.last_composer = result
            if result.confidence is not None:
                self.evaluator_confidence = result.confidence
        else:
            self.any_failure = True

    def mark_failure(self) -> None:
        """Record a failure not tied to a dispatched result (e.g. a workflow
        agent that could not be resolved). Sequential mode counts these."""
        self.any_failure = True

    def outcome(self) -> InvokeOutcome:
        return InvokeOutcome(
            last_ok=self.last_ok,
            last_composer=self.last_composer,
            evaluator_confidence=self.evaluator_confidence,
            any_failure=self.any_failure,
            total_tokens=self.total_tokens,
            prior_findings=tuple(self._prior),
        )
