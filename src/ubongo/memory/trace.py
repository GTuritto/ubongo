"""Typed, render-ready views over a workflow run's execution trace.

`last_n_workflow_runs` (below) builds these from its 4-table join (workflow_runs +
agent_runs + governance_decisions + repair_runs) so the `/trace` renderer reads
fields instead of decoding a nested dict by hand. This module owns the join *and*
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

from ubongo.memory.store import connection, now_iso  # noqa: E402

def append_workflow_run(
    conversation_id: int,
    message_id: int,
    classification: dict,
    workflow: dict,
    execution_mode: str,
    outcome: str,
    started_at: str,
    ended_at: str | None = None,
) -> int:
    import json as _json

    conn = connection()
    cursor = conn.execute(
        """
        INSERT INTO workflow_runs
            (conversation_id, message_id, classification, workflow,
             execution_mode, started_at, ended_at, outcome)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            conversation_id,
            message_id,
            _json.dumps(classification),
            _json.dumps(workflow),
            execution_mode,
            started_at,
            ended_at,
            outcome,
        ),
    )
    return int(cursor.lastrowid)


def update_workflow_run_outcome(
    workflow_run_id: int,
    *,
    outcome: str,
    ended_at: str | None = None,
) -> None:
    """Patch outcome (and optionally ended_at) on a workflow_runs row.

    Phase 9e: workflows are INSERTed with outcome='in_progress' before the
    runner dispatches agents, then UPDATEd to success/failure when done.
    """
    conn = connection()
    conn.execute(
        "UPDATE workflow_runs SET outcome = ?, ended_at = COALESCE(?, ended_at) WHERE id = ?",
        (outcome, ended_at, workflow_run_id),
    )


def append_agent_run(
    workflow_run_id: int,
    *,
    agent: str,
    model: str | None,
    input: dict,
    output: dict,
    confidence: float | None,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    outcome: str,
    started_at: str,
    ended_at: str,
    retried: bool = False,
) -> int:
    """Persist one agent_runs row. Called by the WorkflowRunner per agent dispatch.

    Phase 11d: `retried=True` marks the row as the second attempt at the
    same agent (Repair Agent's single-retry path). The trace renderer
    surfaces this so the operator can tell first attempt from retry.
    """
    import json as _json

    conn = connection()
    cursor = conn.execute(
        """
        INSERT INTO agent_runs
            (workflow_run_id, agent, model, input, output, confidence,
             tokens_in, tokens_out, latency_ms, outcome, started_at, ended_at,
             retried)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workflow_run_id,
            agent,
            model,
            _json.dumps(input),
            _json.dumps(output),
            confidence,
            tokens_in,
            tokens_out,
            latency_ms,
            outcome,
            started_at,
            ended_at,
            1 if retried else 0,
        ),
    )
    return int(cursor.lastrowid)


def append_governance_decision(
    workflow_run_id: int,
    *,
    intent: str | None,
    risk: str | None,
    confidence: float | None,
    reversibility: str | None,
    action: str,
    approval_response: str | None = None,
    decided_at: str | None = None,
) -> int:
    conn = connection()
    cursor = conn.execute(
        """
        INSERT INTO governance_decisions
            (workflow_run_id, intent, risk, confidence, reversibility,
             action, approval_response, decided_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workflow_run_id,
            intent,
            risk,
            confidence,
            reversibility,
            action,
            approval_response,
            decided_at or now_iso(),
        ),
    )
    return int(cursor.lastrowid)


def update_governance_decision(decision_id: int, approval_response: str) -> None:
    """Phase 15b: persist the user's y/n approval onto a governance_decisions
    row written earlier in the turn with approval_response=NULL.

    The row is INSERTed synchronously during master.handle; the interactive
    approval prompt happens after handle() returns, so the response is patched
    in by a second call from the REPL.
    """
    connection().execute(
        "UPDATE governance_decisions SET approval_response = ? WHERE id = ?",
        (approval_response, decision_id),
    )


def append_repair_run(
    workflow_run_id: int,
    *,
    agent: str,
    failure_kind: str,
    original_error: str | None,
    strategy_attempted: str,
    peer_agent: str | None,
    override_model: str | None,
    attempt_index: int,
    outcome: str,
    started_at: str,
    ended_at: str | None,
) -> int:
    """Persist one repair_runs row (Phase 13e). Called by the WorkflowRunner
    after each Repair strategy attempt — recovered, failed, or aborted."""
    conn = connection()
    cursor = conn.execute(
        """
        INSERT INTO repair_runs
            (workflow_run_id, agent, failure_kind, original_error,
             strategy_attempted, peer_agent, override_model,
             attempt_index, outcome, started_at, ended_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workflow_run_id,
            agent,
            failure_kind,
            original_error,
            strategy_attempted,
            peer_agent,
            override_model,
            attempt_index,
            outcome,
            started_at,
            ended_at,
        ),
    )
    return int(cursor.lastrowid)


def repair_runs_for_workflow(workflow_run_id: int) -> list[dict]:
    """Return all repair_runs rows for a workflow_run, in attempt order.

    Each dict carries: id, agent, failure_kind, original_error,
    strategy_attempted, peer_agent, override_model, attempt_index, outcome,
    started_at, ended_at.
    """
    conn = connection()
    rows = conn.execute(
        """
        SELECT id, agent, failure_kind, original_error, strategy_attempted,
               peer_agent, override_model, attempt_index, outcome,
               started_at, ended_at
        FROM repair_runs
        WHERE workflow_run_id = ?
        ORDER BY id
        """,
        (workflow_run_id,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "agent": r["agent"],
            "failure_kind": r["failure_kind"],
            "original_error": r["original_error"],
            "strategy_attempted": r["strategy_attempted"],
            "peer_agent": r["peer_agent"],
            "override_model": r["override_model"],
            "attempt_index": r["attempt_index"],
            "outcome": r["outcome"],
            "started_at": r["started_at"],
            "ended_at": r["ended_at"],
        }
        for r in rows
    ]


def last_n_governance_decisions(n: int = 10) -> list[dict]:
    """Return the last N decisions joined with their workflow_runs for display.

    Each dict carries: id, decided_at, intent, risk, confidence, action,
    persona (extracted from workflow JSON), execution_mode, workflow_run_id.
    """
    import json as _json

    if n <= 0:
        return []
    conn = connection()
    rows = conn.execute(
        """
        SELECT g.id, g.decided_at, g.intent, g.risk, g.confidence, g.action,
               g.reversibility, g.workflow_run_id, w.execution_mode, w.workflow
        FROM governance_decisions g
        JOIN workflow_runs w ON w.id = g.workflow_run_id
        ORDER BY g.decided_at DESC, g.id DESC
        LIMIT ?
        """,
        (n,),
    ).fetchall()
    out: list[dict] = []
    for row in rows:
        persona = None
        try:
            wf = _json.loads(row["workflow"]) if row["workflow"] else {}
            persona = wf.get("persona")
        except Exception:
            persona = None
        out.append({
            "id": row["id"],
            "decided_at": row["decided_at"],
            "intent": row["intent"],
            "risk": row["risk"],
            "confidence": row["confidence"],
            "reversibility": row["reversibility"],
            "action": row["action"],
            "workflow_run_id": row["workflow_run_id"],
            "execution_mode": row["execution_mode"],
            "persona": persona,
        })
    return out


def last_n_workflow_runs(n: int = 1) -> list["WorkflowTrace"]:
    """Return the last N workflow runs as typed, render-ready WorkflowTrace
    views. Used by the /trace REPL command.

    The store owns the join (workflow_runs + agent_runs + governance_decisions +
    repair_runs) *and* the grouping: each repair attempt is attached to the
    failing agent_run it applies to (the first failure row for that agent), so
    the renderer reads fields instead of regrouping repair_runs by hand
    (candidate 03 of the 2026-06-05 architecture review). See memory/trace.py.
    """
    import json as _json

    from ubongo.memory.trace import (
        AgentRunView,
        GovernanceView,
        RepairRunView,
        WorkflowTrace,
    )

    if n <= 0:
        return []
    conn = connection()
    wf_rows = conn.execute(
        """
        SELECT id, conversation_id, message_id, classification, workflow,
               execution_mode, started_at, ended_at, outcome
        FROM workflow_runs
        ORDER BY id DESC
        LIMIT ?
        """,
        (n,),
    ).fetchall()
    if not wf_rows:
        return []
    wf_ids = [row["id"] for row in wf_rows]
    placeholders = ",".join("?" for _ in wf_ids)
    ar_rows = conn.execute(
        f"""
        SELECT id, workflow_run_id, agent, model, confidence, tokens_in,
               tokens_out, latency_ms, outcome, started_at, ended_at, output,
               retried
        FROM agent_runs
        WHERE workflow_run_id IN ({placeholders})
        ORDER BY workflow_run_id, id
        """,
        wf_ids,
    ).fetchall()
    gd_rows = conn.execute(
        f"""
        SELECT id, workflow_run_id, intent, risk, confidence, reversibility, action
        FROM governance_decisions
        WHERE workflow_run_id IN ({placeholders})
        ORDER BY workflow_run_id, id
        """,
        wf_ids,
    ).fetchall()
    rr_rows = conn.execute(
        f"""
        SELECT id, workflow_run_id, agent, failure_kind, original_error,
               strategy_attempted, peer_agent, override_model, attempt_index,
               outcome, started_at, ended_at
        FROM repair_runs
        WHERE workflow_run_id IN ({placeholders})
        ORDER BY workflow_run_id, id
        """,
        wf_ids,
    ).fetchall()

    # Raw agent_runs per workflow (order preserved by the query's ORDER BY id);
    # turned into AgentRunViews in the build loop below.
    ar_by_wf: dict[int, list] = {wf_id: [] for wf_id in wf_ids}
    for row in ar_rows:
        ar_by_wf.setdefault(row["workflow_run_id"], []).append(row)

    gd_by_wf: dict[int, GovernanceView] = {}
    for row in gd_rows:
        gd_by_wf[row["workflow_run_id"]] = GovernanceView(
            id=row["id"],
            action=row["action"],
            confidence=row["confidence"],
            intent=row["intent"],
            risk=row["risk"],
            reversibility=row["reversibility"],
        )

    # Repair attempts grouped by (workflow, agent) so each can be attached to
    # the failing agent_run it applies to.
    rr_by_wf_agent: dict[int, dict[str, list[RepairRunView]]] = {}
    for row in rr_rows:
        rv = RepairRunView(
            agent=row["agent"],
            failure_kind=row["failure_kind"],
            original_error=row["original_error"],
            strategy_attempted=row["strategy_attempted"],
            peer_agent=row["peer_agent"],
            override_model=row["override_model"],
            attempt_index=row["attempt_index"],
            outcome=row["outcome"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
        )
        rr_by_wf_agent.setdefault(row["workflow_run_id"], {}).setdefault(
            row["agent"], []
        ).append(rv)

    out: list[WorkflowTrace] = []
    for row in wf_rows:
        try:
            cls = _json.loads(row["classification"]) if row["classification"] else {}
        except Exception:
            cls = {}
        try:
            wf = _json.loads(row["workflow"]) if row["workflow"] else {}
        except Exception:
            wf = {}

        repairs_for_agent = rr_by_wf_agent.get(row["id"], {})
        attached: set[str] = set()
        agent_views: list[AgentRunView] = []
        for ar in ar_by_wf.get(row["id"], []):
            err = None
            try:
                out_json = _json.loads(ar["output"]) if ar["output"] else {}
                err = out_json.get("error")
            except Exception:
                err = None
            # Attach this agent's repair attempts under its FIRST failure row,
            # not under a peer's later success row (the grouping the /trace
            # renderer used to do inline).
            agent_name = ar["agent"]
            repair_runs: tuple[RepairRunView, ...] = ()
            if (
                ar["outcome"] == "failure"
                and agent_name in repairs_for_agent
                and agent_name not in attached
            ):
                repair_runs = tuple(repairs_for_agent[agent_name])
                attached.add(agent_name)
            agent_views.append(AgentRunView(
                agent=agent_name,
                model=ar["model"],
                confidence=ar["confidence"],
                tokens_in=ar["tokens_in"],
                tokens_out=ar["tokens_out"],
                latency_ms=ar["latency_ms"],
                outcome=ar["outcome"],
                started_at=ar["started_at"],
                ended_at=ar["ended_at"],
                error=err,
                retried=bool(ar["retried"]),
                repair_runs=repair_runs,
            ))

        out.append(WorkflowTrace(
            id=row["id"],
            conversation_id=row["conversation_id"],
            message_id=row["message_id"],
            classification=cls,
            workflow=wf,
            execution_mode=row["execution_mode"],
            outcome=row["outcome"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            agent_runs=tuple(agent_views),
            governance=gd_by_wf.get(row["id"]),
        ))
    return out




def recent_workflow_classifications(limit: int = 200) -> list[dict]:
    """Recent turns' classification JSON joined to the user message, newest
    first — the raw material for authoring gap inference. Each dict is
    {classification: dict, message: str}."""
    import json as _json

    conn = connection()
    rows = conn.execute(
        "SELECT w.classification AS cls, m.content AS msg "
        "FROM workflow_runs w JOIN messages m ON m.id = w.message_id "
        "ORDER BY w.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        try:
            cls = _json.loads(r["cls"]) if r["cls"] else {}
        except (TypeError, ValueError):
            cls = {}
        out.append({"classification": cls if isinstance(cls, dict) else {}, "message": r["msg"] or ""})
    return out
