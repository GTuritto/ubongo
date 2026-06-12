from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.memory import store
from ubongo.memory import trace  # noqa: E402


@pytest.fixture
def db(tmp_path: Path):
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")


def test_bootstrap_creates_all_tables(db) -> None:
    conn = store.connection()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r["name"] for r in rows}
    expected = {
        "conversations", "messages", "summaries", "sessions", "facts",
        "workflow_runs", "agent_runs", "governance_decisions",
        "evolution_lineage", "evolution_evaluations", "pending_promotions",
        "active_evolutions", "notification_queue", "vault_links",
    }
    assert expected.issubset(names)


def test_start_conversation_returns_id(db) -> None:
    cid = store.start_conversation("architect")
    conv = store.get_conversation(cid)
    assert conv is not None
    assert conv.active_persona == "architect"
    assert conv.ended_at is None


def test_end_conversation_sets_ended_at(db) -> None:
    cid = store.start_conversation("casual")
    store.end_conversation(cid)
    conv = store.get_conversation(cid)
    assert conv is not None
    assert conv.ended_at is not None


def test_append_and_recall_messages(db) -> None:
    cid = store.start_conversation("architect")
    for i in range(3):
        store.append_message(cid, "user", f"q{i}", persona="architect")
        store.append_message(cid, "assistant", f"a{i}", persona="architect", model="m", tokens_in=10, tokens_out=5)
    msgs = store.last_n_messages(cid, 4)
    assert len(msgs) == 4
    # Last 4 messages chronologically: q1, a1, q2, a2
    assert [m.content for m in msgs] == ["q1", "a1", "q2", "a2"]


def test_last_n_messages_chronological(db) -> None:
    cid = store.start_conversation("architect")
    ids = [store.append_message(cid, "user", str(i)) for i in range(5)]
    msgs = store.last_n_messages(cid, 10)
    assert [m.id for m in msgs] == ids


def test_summary_persistence_and_retrieval(db) -> None:
    cid = store.start_conversation("architect")
    for i in range(3):
        store.append_message(cid, "user", f"q{i}")
    sid = store.persist_summary(cid, 1, 2, "summary text", "default")
    latest = store.latest_summary(cid)
    assert latest is not None
    assert latest.id == sid
    assert latest.content == "summary text"
    assert latest.covers_to_message_id == 2


def test_count_messages_since_summary(db) -> None:
    cid = store.start_conversation("architect")
    ids = [store.append_message(cid, "user", str(i)) for i in range(5)]
    assert store.count_messages_since_summary(cid) == 5
    store.persist_summary(cid, 1, ids[2], "s", "default")
    assert store.count_messages_since_summary(cid) == 2


def test_session_upsert_insert_path(db) -> None:
    cid = store.start_conversation("architect")
    store.upsert_session(active_persona="architect", current_conversation_id=cid, auto_mode=True)
    sess = store.get_session()
    assert sess is not None
    assert sess.active_persona == "architect"
    assert sess.current_conversation_id == cid
    assert sess.auto_mode is True


def test_session_upsert_update_preserves_unspecified_fields(db) -> None:
    cid = store.start_conversation("architect")
    store.upsert_session(active_persona="architect", current_conversation_id=cid, auto_mode=True)
    store.upsert_session(active_persona="casual")  # only persona changes
    sess = store.get_session()
    assert sess is not None
    assert sess.active_persona == "casual"
    assert sess.current_conversation_id == cid  # preserved
    assert sess.auto_mode is True  # preserved


def test_max_message_id(db) -> None:
    cid = store.start_conversation("architect")
    assert store.max_message_id(cid) == 0
    last = 0
    for _ in range(3):
        last = store.append_message(cid, "user", "x")
    assert store.max_message_id(cid) == last


def test_messages_in_range(db) -> None:
    cid = store.start_conversation("architect")
    ids = [store.append_message(cid, "user", str(i)) for i in range(5)]
    msgs = store.messages_in_range(cid, ids[1], ids[3])
    assert [m.id for m in msgs] == ids[1:4]


def test_now_iso_uses_fake_now_when_set(db, monkeypatch) -> None:
    monkeypatch.setenv("UBONGO_FAKE_NOW", "2030-01-01T00:00:00+00:00")
    iso = store.now_iso()
    assert iso.startswith("2030-01-01T00:00:00")


# --- session timeout / current-or-new conversation ---


def test_current_or_new_conversation_creates_first_one(db) -> None:
    cid = store.current_or_new_conversation("architect")
    sess = store.get_session()
    assert sess is not None
    assert sess.current_conversation_id == cid
    assert sess.active_persona == "architect"


def test_current_or_new_conversation_continues_within_timeout(db, monkeypatch) -> None:
    monkeypatch.setenv("UBONGO_FAKE_NOW", "2030-01-01T12:00:00+00:00")
    cid_1 = store.current_or_new_conversation("architect")
    monkeypatch.setenv("UBONGO_FAKE_NOW", "2030-01-01T12:25:00+00:00")  # 25 min later
    cid_2 = store.current_or_new_conversation("architect")
    assert cid_1 == cid_2


def test_recall_inherits_summary_from_previous_conversation(db) -> None:
    """When a new conversation has no summary, recall falls back to the most
    recent summary from any other conversation. Cross-session memory."""
    cid_old = store.start_conversation("casual")
    store.append_message(cid_old, "user", "my birthday is March 15")
    store.persist_summary(cid_old, 1, 1, "User said their birthday is March 15.", "default")
    store.end_conversation(cid_old)

    cid_new = store.start_conversation("casual")
    store.append_message(cid_new, "user", "hey")
    ctx = store.recall(cid_new)
    assert ctx.summary_text is not None
    assert "March 15" in ctx.summary_text
    # The current conversation has no summary, but the inherited one shows up.


def test_recall_prefers_current_conversation_summary_over_inherited(db) -> None:
    cid_old = store.start_conversation("casual")
    store.append_message(cid_old, "user", "old fact")
    store.persist_summary(cid_old, 1, 1, "OLD SUMMARY", "default")
    cid_new = store.start_conversation("casual")
    for _ in range(2):
        store.append_message(cid_new, "user", "x")
    store.persist_summary(cid_new, 1, 2, "NEW SUMMARY", "default")
    ctx = store.recall(cid_new)
    assert ctx.summary_text == "NEW SUMMARY"


def test_recall_returns_summary_and_messages(db) -> None:
    cid = store.start_conversation("architect")
    for i in range(3):
        store.append_message(cid, "user", f"q{i}")
    store.persist_summary(cid, 1, 2, "old summary", "default")
    ctx = store.recall(cid)
    assert ctx.summary_text == "old summary"
    assert [m.content for m in ctx.messages] == ["q0", "q1", "q2"]


def test_recall_emits_after_recall_event(db) -> None:
    from ubongo import events
    seen: list[dict] = []
    events.register("after_recall", seen.append)
    cid = store.start_conversation("architect")
    store.append_message(cid, "user", "hi")
    store.recall(cid)
    assert seen
    assert seen[-1]["conversation_id"] == cid
    events.clear()


def test_current_or_new_conversation_starts_new_after_timeout(db, monkeypatch) -> None:
    monkeypatch.setenv("UBONGO_FAKE_NOW", "2030-01-01T12:00:00+00:00")
    cid_1 = store.current_or_new_conversation("architect")
    monkeypatch.setenv("UBONGO_FAKE_NOW", "2030-01-01T12:31:00+00:00")  # 31 min later
    cid_2 = store.current_or_new_conversation("casual")
    assert cid_1 != cid_2
    # Previous conversation closed at the previous last_message_at, not 'now'.
    prev = store.get_conversation(cid_1)
    assert prev is not None
    assert prev.ended_at is not None
    assert prev.ended_at.startswith("2030-01-01T12:00:00")


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
