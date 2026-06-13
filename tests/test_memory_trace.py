"""Trace-table persistence tests (split from test_memory_store.py, v0.5 phase 02).

Covers the four trace tables now owned by memory/trace.py: workflow_runs,
agent_runs, governance_decisions, repair_runs — including the
last_n_workflow_runs builder's repair-to-failing-agent grouping.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.memory import store  # noqa: E402
from ubongo.memory import trace  # noqa: E402


@pytest.fixture
def db(tmp_path: Path):
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")


def test_workflow_run_and_governance_decision_round_trip(db) -> None:
    cid = store.start_conversation("architect")
    msg_id = store.append_message(cid, "user", "design a circuit breaker", persona="architect")
    wf_id = trace.append_workflow_run(
        conversation_id=cid,
        message_id=msg_id,
        classification={"intent": "technical", "confidence": 0.9},
        workflow={"persona": "architect", "execution_mode": "sequential"},
        execution_mode="sequential",
        outcome="success",
        started_at=store.now_iso(),
        ended_at=store.now_iso(),
    )
    assert isinstance(wf_id, int)
    gd_id = trace.append_governance_decision(
        workflow_run_id=wf_id,
        intent="technical",
        risk="low",
        confidence=0.9,
        reversibility=None,
        action="auto",
    )
    assert isinstance(gd_id, int)

    decisions = trace.last_n_governance_decisions(10)
    assert len(decisions) == 1
    d = decisions[0]
    assert d["id"] == gd_id
    assert d["workflow_run_id"] == wf_id
    assert d["intent"] == "technical"
    assert d["action"] == "auto"
    assert d["persona"] == "architect"  # extracted from workflow JSON
    assert d["execution_mode"] == "sequential"


def test_last_n_governance_decisions_returns_newest_first(db) -> None:
    cid = store.start_conversation("casual")
    msg_id = store.append_message(cid, "user", "x")
    ids = []
    for _ in range(3):
        wf_id = trace.append_workflow_run(
            conversation_id=cid,
            message_id=msg_id,
            classification={"intent": "casual"},
            workflow={"persona": "casual"},
            execution_mode="sequential",
            outcome="success",
            started_at=store.now_iso(),
        )
        gd_id = trace.append_governance_decision(
            workflow_run_id=wf_id,
            intent="casual",
            risk="low",
            confidence=0.9,
            reversibility=None,
            action="auto",
        )
        ids.append(gd_id)
    out = trace.last_n_governance_decisions(2)
    assert [d["id"] for d in out] == [ids[2], ids[1]]


def test_last_n_governance_decisions_empty(db) -> None:
    assert trace.last_n_governance_decisions(10) == []


# --- Phase 13e: repair_runs ---


def _seed_workflow(db) -> int:
    cid = store.start_conversation("architect")
    msg_id = store.append_message(cid, "user", "trigger", persona="architect")
    return trace.append_workflow_run(
        conversation_id=cid,
        message_id=msg_id,
        classification={"intent": "technical"},
        workflow={"persona": "architect", "execution_mode": "sequential"},
        execution_mode="sequential",
        outcome="in_progress",
        started_at=store.now_iso(),
    )


def test_append_repair_run_round_trips(db) -> None:
    wf_id = _seed_workflow(db)
    rr_id = trace.append_repair_run(
        workflow_run_id=wf_id,
        agent="evaluator",
        failure_kind="parse_error",
        original_error="evaluator_parse_error",
        strategy_attempted="retry_same_model_variant_prompt",
        peer_agent=None,
        override_model=None,
        attempt_index=0,
        outcome="recovered",
        started_at=store.now_iso(),
        ended_at=store.now_iso(),
    )
    assert isinstance(rr_id, int)

    rows = trace.repair_runs_for_workflow(wf_id)
    assert len(rows) == 1
    assert rows[0]["id"] == rr_id
    assert rows[0]["failure_kind"] == "parse_error"
    assert rows[0]["strategy_attempted"] == "retry_same_model_variant_prompt"
    assert rows[0]["outcome"] == "recovered"


def test_repair_runs_for_workflow_orders_by_id(db) -> None:
    wf_id = _seed_workflow(db)
    for i, strategy in enumerate([
        "retry_same_model_variant_prompt",
        "retry_different_model_same_prompt",
        "replace_with_peer",
    ]):
        trace.append_repair_run(
            workflow_run_id=wf_id,
            agent="evaluator",
            failure_kind="parse_error",
            original_error="evaluator_parse_error",
            strategy_attempted=strategy,
            peer_agent="research" if strategy == "replace_with_peer" else None,
            override_model=None,
            attempt_index=i,
            outcome="failed",
            started_at=store.now_iso(),
            ended_at=store.now_iso(),
        )
    rows = trace.repair_runs_for_workflow(wf_id)
    assert [r["attempt_index"] for r in rows] == [0, 1, 2]
    assert rows[2]["peer_agent"] == "research"


def _seed_agent_run(wf_id: int, agent: str, outcome: str) -> int:
    return trace.append_agent_run(
        wf_id,
        agent=agent,
        model="m",
        input={},
        output={"error": "critic_no_candidate"} if outcome == "failure" else {},
        confidence=None,
        tokens_in=0,
        tokens_out=0,
        latency_ms=1,
        outcome=outcome,
        started_at=store.now_iso(),
        ended_at=store.now_iso(),
    )


def test_last_n_workflow_runs_attaches_repair_to_failing_agent(db) -> None:
    # The repair attempt is grouped under the FAILING critic row, not the
    # peer's later success row (the grouping the /trace renderer used to do).
    wf_id = _seed_workflow(db)
    _seed_agent_run(wf_id, "critic", "failure")
    _seed_agent_run(wf_id, "architect", "success")  # the peer that replaced it
    trace.append_repair_run(
        workflow_run_id=wf_id,
        agent="critic",
        failure_kind="precondition_missing",
        original_error="critic_no_candidate",
        strategy_attempted="replace_with_peer",
        peer_agent="architect",
        override_model=None,
        attempt_index=0,
        outcome="recovered",
        started_at=store.now_iso(),
        ended_at=store.now_iso(),
    )
    rows = trace.last_n_workflow_runs(1)
    assert len(rows) == 1
    by_agent = {ar.agent: ar for ar in rows[0].agent_runs}
    # Repair attached to the failing critic row...
    assert len(by_agent["critic"].repair_runs) == 1
    assert by_agent["critic"].repair_runs[0].peer_agent == "architect"
    # ...and not to the peer's success row.
    assert by_agent["architect"].repair_runs == ()
