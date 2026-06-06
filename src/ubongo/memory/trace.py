"""Typed, render-ready views over a workflow run's execution trace.

`store.last_n_workflow_runs` builds these from its 4-table join (workflow_runs +
agent_runs + governance_decisions + repair_runs) so the `/trace` renderer reads
fields instead of decoding a nested dict by hand. The store owns the join *and*
the grouping: each repair attempt is attached to the failing agent_run it
applies to, so the renderer no longer regroups repair_runs or tracks which it
has printed (candidate 03 of the 2026-06-05 architecture review).

The classification/workflow JSON blobs stay dicts (free-form, many optional
keys); the leaky `wf["agents"]` / `cls["intent"]` navigation is wrapped in
convenience accessors on WorkflowTrace.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RepairRunView:
    """One repair attempt (a repair_runs row), attached under its failing agent."""

    agent: str
    failure_kind: str
    original_error: str | None
    strategy_attempted: str
    peer_agent: str | None
    override_model: str | None
    attempt_index: int
    outcome: str  # recovered | failed | aborted
    started_at: str
    ended_at: str | None


@dataclass(frozen=True)
class AgentRunView:
    """One agent dispatch (an agent_runs row). `repair_runs` is non-empty only
    on the failing row the builder attaches recovery attempts to."""

    agent: str
    model: str | None
    confidence: float | None
    tokens_in: int
    tokens_out: int
    latency_ms: int | None
    outcome: str
    started_at: str
    ended_at: str | None
    error: str | None
    retried: bool
    repair_runs: tuple[RepairRunView, ...] = ()


@dataclass(frozen=True)
class GovernanceView:
    """The governance_decision for a workflow run."""

    id: int
    action: str
    confidence: float | None
    intent: str | None
    risk: str | None
    reversibility: str | None


@dataclass(frozen=True)
class WorkflowTrace:
    """One workflow run with its agents, governance decision, and (per-agent)
    repair attempts — grouped and typed so callers read fields, not schema."""

    id: int
    conversation_id: int | None
    message_id: int | None
    classification: dict  # parsed JSON; use the accessors below to read it
    workflow: dict        # parsed JSON; ditto
    execution_mode: str
    outcome: str
    started_at: str
    ended_at: str | None
    agent_runs: tuple[AgentRunView, ...] = ()
    governance: GovernanceView | None = None

    # --- convenience accessors so callers don't index the JSON blobs ---

    @property
    def persona(self) -> str | None:
        return self.workflow.get("persona")

    @property
    def agents(self) -> list[str]:
        return self.workflow.get("agents") or []

    @property
    def intent(self) -> str | None:
        return self.classification.get("intent")

    @property
    def tone(self) -> str | None:
        return self.classification.get("tone")

    @property
    def task_type(self) -> str | None:
        return self.classification.get("task_type")

    @property
    def risk(self) -> str | None:
        return self.classification.get("risk")

    @property
    def cls_confidence(self):
        return self.classification.get("confidence")
