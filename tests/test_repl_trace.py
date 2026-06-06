from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context, events, skills  # noqa: E402
from ubongo.memory import store, vault  # noqa: E402
from ubongo.repl import _parse_trace_command, _render_trace  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")
    vault.set_vault_root(tmp_path / "vault")
    skills.set_skills_dir(None)
    skills.reload()
    context.reload()
    events.clear()
    yield
    events.clear()
    skills.set_skills_dir(None)
    skills.reload()
    context.reload()
    store.set_db_path(None)
    vault.set_vault_root(None)


def _seed_trace_row() -> int:
    conv = store.current_or_new_conversation("architect")
    msg = store.append_message(conv, "user", "design a circuit breaker", persona="architect")
    wf = store.append_workflow_run(
        conversation_id=conv,
        message_id=msg,
        classification={
            "intent": "technical", "tone": "neutral", "task_type": "question",
            "risk": "low", "confidence": 0.78, "suggested_skill": None,
        },
        workflow={
            "persona": "architect", "model": "m", "skill_name": None,
            "execution_mode": "sequential", "agents": ["architect", "evaluator"],
        },
        execution_mode="sequential",
        outcome="success",
        started_at=store.now_iso(),
    )
    started = store.now_iso()
    store.append_agent_run(
        workflow_run_id=wf, agent="architect", model="anthropic/claude-sonnet-4.5",
        input={"len": 30}, output={"text_len": 120}, confidence=None,
        tokens_in=820, tokens_out=540, latency_ms=1840,
        outcome="success", started_at=started, ended_at=store.now_iso(),
    )
    store.append_agent_run(
        workflow_run_id=wf, agent="evaluator", model="anthropic/claude-sonnet-4.5",
        input={"len": 120}, output={"text_len": 42}, confidence=0.83,
        tokens_in=380, tokens_out=44, latency_ms=210,
        outcome="success", started_at=started, ended_at=store.now_iso(),
    )
    store.append_governance_decision(
        workflow_run_id=wf, intent="technical", risk="low",
        confidence=0.83, reversibility="reversible", action="auto",
    )
    return wf


# --- parser ---


def test_parse_trace_default_returns_one():
    assert _parse_trace_command("/trace") == 1


def test_parse_trace_with_count():
    assert _parse_trace_command("/trace 5") == 5


def test_parse_trace_rejects_garbage():
    assert _parse_trace_command("/trace foo") is None


def test_parse_trace_rejects_zero_or_negative():
    assert _parse_trace_command("/trace 0") is None
    assert _parse_trace_command("/trace -2") is None


# --- renderer ---


def test_render_trace_no_rows():
    assert _render_trace(1) == "No traces yet."


def test_render_trace_includes_classification_workflow_agents_governance():
    wf_id = _seed_trace_row()
    out = _render_trace(1)
    assert f"workflow_run #{wf_id}" in out
    assert "intent=technical" in out
    assert "persona=architect" in out
    assert "agents=[architect,evaluator]" in out
    # both agent rows in order
    arch_idx = out.find("architect ")
    eval_idx = out.find("evaluator ")
    assert 0 <= arch_idx < eval_idx
    # evaluator confidence shows
    assert "conf=0.83" in out
    # governance line, with the Phase-14 reversibility field
    assert "action=auto" in out
    assert "rev=reversible" in out


def test_render_trace_renders_repair_line_under_failing_agent():
    """Phase 13e: when a workflow has repair_runs, the renderer attaches a
    `repair: kind=… strategy=… outcome=… peer=…` line indented under the
    affected agent_runs row."""
    conv = store.current_or_new_conversation("architect")
    msg = store.append_message(conv, "user", "x", persona="architect")
    wf = store.append_workflow_run(
        conversation_id=conv, message_id=msg,
        classification={"intent": "technical", "confidence": 0.7},
        workflow={"persona": "architect", "execution_mode": "sequential",
                  "agents": ["critic", "architect"]},
        execution_mode="sequential",
        outcome="repaired",
        started_at=store.now_iso(),
    )
    started = store.now_iso()
    store.append_agent_run(
        workflow_run_id=wf, agent="critic", model="m",
        input={}, output={"error": "critic_no_candidate"},
        confidence=None, tokens_in=0, tokens_out=0, latency_ms=10,
        outcome="failure", started_at=started, ended_at=store.now_iso(),
    )
    store.append_agent_run(
        workflow_run_id=wf, agent="architect", model="m",
        input={}, output={}, confidence=None,
        tokens_in=10, tokens_out=20, latency_ms=100,
        outcome="success", started_at=started, ended_at=store.now_iso(),
        retried=True,
    )
    store.append_repair_run(
        workflow_run_id=wf, agent="critic",
        failure_kind="precondition_missing",
        original_error="critic_no_candidate",
        strategy_attempted="replace_with_peer",
        peer_agent="architect", override_model=None,
        attempt_index=0, outcome="recovered",
        started_at=started, ended_at=store.now_iso(),
    )
    out = _render_trace(1)
    # Repair line present and includes the key fields.
    assert "repair: kind=precondition_missing" in out
    assert "strategy=replace_with_peer" in out
    assert "outcome=recovered" in out
    assert "peer=architect" in out
    # Indented under the critic failure row (the failing agent_run).
    critic_idx = out.find("critic ")
    repair_idx = out.find("repair: kind=")
    assert 0 <= critic_idx < repair_idx


def test_render_trace_multi():
    # seed two rows
    _seed_trace_row()
    _seed_trace_row()
    out = _render_trace(2)
    # two workflow_run headers
    assert out.count("--- workflow_run #") == 2


# --- store helper ---


def test_last_n_workflow_runs_returns_empty_when_db_empty():
    assert store.last_n_workflow_runs(1) == []


def test_last_n_workflow_runs_orders_desc_and_joins_agent_runs():
    first = _seed_trace_row()
    second = _seed_trace_row()
    rows = store.last_n_workflow_runs(2)
    assert [r.id for r in rows] == [second, first]
    assert len(rows[0].agent_runs) == 2
    assert rows[0].governance.action == "auto"
