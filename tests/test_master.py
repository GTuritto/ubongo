from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context, events, master, skills  # noqa: E402
from ubongo.classifier import Classification  # noqa: E402
from ubongo.llm import CompletionResult, LLMError  # noqa: E402
from ubongo.master import Context, MasterAgent, Workflow, WorkflowResult  # noqa: E402
from ubongo.memory import store, vault  # noqa: E402


def _completion(text: str = "ok") -> CompletionResult:
    return CompletionResult(text=text, model="test-model", tokens_in=12, tokens_out=8, latency_ms=4, attempts=1)


def _classification(**overrides) -> Classification:
    base = {
        "intent": "technical",
        "tone": "neutral",
        "task_type": "question",
        "suggested_skill": None,
        "risk": "low",
        "confidence": 0.9,
    }
    base.update(overrides)
    return Classification(**base)


@pytest.fixture(autouse=True)
def _clean_env(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")
    vault.set_vault_root(tmp_path / "vault")
    skills.set_skills_dir(None)
    skills.reload()
    context.reload()
    events.clear()
    events.register("after_send", vault._after_send_handler)
    yield
    events.clear()
    events.register("after_send", vault._after_send_handler)
    skills.set_skills_dir(None)
    skills.reload()
    context.reload()
    store.set_db_path(None)
    vault.set_vault_root(None)


# --- classify ---


def test_classify_delegates_to_classifier_module():
    agent = MasterAgent()
    with patch("ubongo.master.classifier.classify", return_value=_classification(intent="casual")) as m:
        ctx = Context(conversation_id=None, persona="casual", auto_mode=True, pending_skill=None)
        out = agent.classify("hi", ctx)
    assert out.intent == "casual"
    m.assert_called_once_with("hi")


# --- plan ---


def test_plan_keeps_current_persona_when_auto_off():
    agent = MasterAgent()
    ctx = Context(conversation_id=None, persona="architect", auto_mode=False, pending_skill=None)
    wf = agent.plan(_classification(intent="casual", confidence=0.95), ctx)
    assert wf.persona == "architect"  # auto_mode=False -> hysteresis bypass
    assert wf.skill_name is None
    assert wf.execution_mode == "sequential"
    assert wf.agents == ("persona:architect",)


def test_plan_applies_hysteresis_below_threshold():
    agent = MasterAgent()
    ctx = Context(conversation_id=None, persona="architect", auto_mode=True, pending_skill=None)
    wf = agent.plan(_classification(intent="casual", confidence=0.5), ctx)
    # confidence < 0.7 -> hysteresis keeps current architect
    assert wf.persona == "architect"


def test_plan_switches_persona_above_threshold():
    agent = MasterAgent()
    ctx = Context(conversation_id=None, persona="architect", auto_mode=True, pending_skill=None)
    wf = agent.plan(_classification(intent="casual", confidence=0.95), ctx)
    assert wf.persona == "casual"


def test_plan_honors_pending_skill_over_suggested():
    agent = MasterAgent()
    ctx = Context(
        conversation_id=None,
        persona="architect",
        auto_mode=True,
        pending_skill="summarize-conversation",
    )
    wf = agent.plan(
        _classification(suggested_skill=None, confidence=0.95),
        ctx,
    )
    assert wf.skill_name == "summarize-conversation"


def test_plan_dispatches_before_and_after_events():
    agent = MasterAgent()
    seen: list[str] = []
    events.register("before_plan", lambda _p: seen.append("before"))
    events.register("after_plan", lambda _p: seen.append("after"))
    ctx = Context(conversation_id=None, persona="casual", auto_mode=False, pending_skill=None)
    agent.plan(_classification(), ctx)
    assert seen == ["before", "after"]


# --- execute ---


def test_execute_returns_workflow_result_on_success():
    agent = MasterAgent()
    store.bootstrap()
    conv_id = store.current_or_new_conversation("casual")
    store.append_message(conv_id, "user", "hi", persona="casual")
    ctx = Context(conversation_id=conv_id, persona="casual", auto_mode=False, pending_skill=None)
    wf = Workflow(
        persona="casual",
        model="test-model",
        skill_name=None,
        execution_mode="sequential",
        agents=("persona:casual",),
    )
    with patch("ubongo.master.complete", return_value=_completion("hello back")):
        result = agent.execute(wf, ctx, "hi")
    assert result.ok is True
    assert result.text == "hello back"
    assert result.tokens_in == 12


def test_execute_returns_ok_false_on_llm_error():
    agent = MasterAgent()
    store.bootstrap()
    conv_id = store.current_or_new_conversation("casual")
    store.append_message(conv_id, "user", "hi", persona="casual")
    ctx = Context(conversation_id=conv_id, persona="casual", auto_mode=False, pending_skill=None)
    wf = Workflow(
        persona="casual",
        model="test-model",
        skill_name=None,
        execution_mode="sequential",
        agents=("persona:casual",),
    )
    with patch("ubongo.master.complete", side_effect=LLMError("boom", cause=RuntimeError("nope"))):
        result = agent.execute(wf, ctx, "hi")
    assert result.ok is False
    assert "Sorry, I couldn't reach the model" in result.text


def test_execute_dispatches_before_and_after_events():
    agent = MasterAgent()
    store.bootstrap()
    conv_id = store.current_or_new_conversation("casual")
    store.append_message(conv_id, "user", "hi", persona="casual")
    seen: list[str] = []
    events.register("before_execute", lambda _p: seen.append("before"))
    events.register("after_execute", lambda _p: seen.append("after"))
    ctx = Context(conversation_id=conv_id, persona="casual", auto_mode=False, pending_skill=None)
    wf = Workflow(
        persona="casual",
        model="test-model",
        skill_name=None,
        execution_mode="sequential",
        agents=("persona:casual",),
    )
    with patch("ubongo.master.complete", return_value=_completion()):
        agent.execute(wf, ctx, "hi")
    assert seen == ["before", "after"]


# --- decide ---


def test_decide_returns_auto_stub():
    agent = MasterAgent()
    ctx = Context(conversation_id=1, persona="casual", auto_mode=False, pending_skill=None)
    result = WorkflowResult(text="ok", ok=True, tokens_in=1, tokens_out=1, model="m", latency_ms=1)
    decision = agent.decide(_classification(), result, ctx)
    assert decision.action == "auto"


def test_decide_dispatches_govern_events():
    agent = MasterAgent()
    seen: list[str] = []
    events.register("before_govern", lambda _p: seen.append("before"))
    events.register("after_govern", lambda _p: seen.append("after"))
    ctx = Context(conversation_id=1, persona="casual", auto_mode=False, pending_skill=None)
    result = WorkflowResult(text="ok", ok=True, tokens_in=1, tokens_out=1, model="m", latency_ms=1)
    agent.decide(_classification(), result, ctx)
    assert seen == ["before", "after"]


def test_decide_falls_back_on_internal_error():
    agent = MasterAgent()
    ctx = Context(conversation_id=1, persona="casual", auto_mode=False, pending_skill=None)
    result = WorkflowResult(text="ok", ok=True, tokens_in=1, tokens_out=1, model="m", latency_ms=1)
    with patch("ubongo.master.governance_decide", side_effect=RuntimeError("matrix down")):
        decision = agent.decide(_classification(), result, ctx)
    assert decision.action == "auto"
    assert decision.reason == "fallback_on_error"


# --- compose ---


def test_compose_is_passthrough_in_phase_8():
    agent = MasterAgent()
    ctx = Context(conversation_id=1, persona="casual", auto_mode=False, pending_skill=None)
    wf = Workflow(persona="casual", model="m", skill_name=None, execution_mode="sequential", agents=("persona:casual",))
    result = WorkflowResult(text="the answer", ok=True, tokens_in=1, tokens_out=1, model="m", latency_ms=1)
    out = agent.compose(wf, result, ctx)
    assert out == "the answer"


def test_compose_dispatches_compose_events():
    agent = MasterAgent()
    seen: list[str] = []
    events.register("before_compose", lambda _p: seen.append("before"))
    events.register("after_compose", lambda _p: seen.append("after"))
    ctx = Context(conversation_id=1, persona="casual", auto_mode=False, pending_skill=None)
    wf = Workflow(persona="casual", model="m", skill_name=None, execution_mode="sequential", agents=("persona:casual",))
    result = WorkflowResult(text="x", ok=True, tokens_in=1, tokens_out=1, model="m", latency_ms=1)
    agent.compose(wf, result, ctx)
    assert seen == ["before", "after"]


# --- handle (end-to-end) ---


def test_handle_happy_path_returns_response_with_token():
    with patch("ubongo.master.complete", return_value=_completion("hello back")):
        response = master.handle("hi", "casual", auto_mode=False)
    assert response.ok is True
    assert response.text == "hello back"
    assert response.persona == "casual"
    assert response.skill_name is None
    assert response.delivery_token is not None


def test_handle_error_path_returns_polite_message_and_ok_false():
    with patch("ubongo.master.complete", side_effect=LLMError("boom", cause=RuntimeError("nope"))):
        response = master.handle("hi", "casual", auto_mode=False)
    assert response.ok is False
    assert "Sorry, I couldn't reach the model" in response.text


def test_handle_appends_user_and_assistant_messages_on_success():
    with patch("ubongo.master.complete", return_value=_completion("hello back")):
        master.handle("hi", "casual", auto_mode=False)
    messages = store.last_n_messages(1, 10)
    roles = [m.role for m in messages]
    assert roles == ["user", "assistant"]
    assert messages[1].content == "hello back"


def test_handle_does_not_append_assistant_on_failure():
    with patch("ubongo.master.complete", side_effect=LLMError("boom", cause=RuntimeError("nope"))):
        master.handle("hi", "casual", auto_mode=False)
    messages = store.last_n_messages(1, 10)
    roles = [m.role for m in messages]
    assert roles == ["user"]  # no assistant row written on error


def test_handle_returns_skill_name_when_pending_skill_set():
    with patch("ubongo.master.complete", return_value=_completion("ok")):
        response = master.handle(
            "wrap this up",
            "operator",
            auto_mode=False,
            pending_skill="summarize-conversation",
        )
    assert response.skill_name == "summarize-conversation"


# --- persistence (8d) ---


def _query_one(sql: str):
    conn = store.connection()
    return conn.execute(sql).fetchone()


def test_handle_persists_workflow_run_and_governance_decision_on_success():
    with patch("ubongo.master.complete", return_value=_completion("ok")):
        master.handle("hi", "casual", auto_mode=False)

    wf = _query_one(
        "SELECT id, conversation_id, execution_mode, outcome FROM workflow_runs"
    )
    assert wf is not None
    assert wf["execution_mode"] == "sequential"
    assert wf["outcome"] == "success"

    gd = _query_one(
        "SELECT workflow_run_id, intent, risk, action FROM governance_decisions"
    )
    assert gd is not None
    assert gd["workflow_run_id"] == wf["id"]
    assert gd["action"] == "auto"


def test_handle_persists_workflow_run_with_failure_outcome_on_llm_error():
    with patch("ubongo.master.complete", side_effect=LLMError("boom", cause=RuntimeError("nope"))):
        master.handle("hi", "casual", auto_mode=False)

    wf = _query_one("SELECT outcome FROM workflow_runs")
    assert wf is not None
    assert wf["outcome"] == "failure"
    # decision still recorded with action=auto (Phase 14 will downgrade)
    gd = _query_one("SELECT action FROM governance_decisions")
    assert gd["action"] == "auto"


def test_handle_emits_master_decision_log(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="ubongo.master")
    with patch("ubongo.master.complete", return_value=_completion("ok")):
        master.handle("hi", "casual", auto_mode=False)

    md_records = [r for r in caplog.records if r.msg == "master_decision"]
    assert len(md_records) == 1
    rec = md_records[0]
    # All documented fields present
    for field in (
        "intent", "tone", "task_type", "risk", "confidence",
        "persona", "skill", "execution_mode", "action",
        "workflow_run_id", "decision_id", "conversation_id",
    ):
        assert hasattr(rec, field), f"missing {field}"
    assert rec.action == "auto"
    assert rec.execution_mode == "sequential"


def test_workflow_run_classification_json_round_trips():
    import json
    with patch("ubongo.master.complete", return_value=_completion("ok")):
        master.handle("design a circuit breaker", "architect", auto_mode=False)
    row = _query_one("SELECT classification, workflow FROM workflow_runs")
    cls = json.loads(row["classification"])
    wf = json.loads(row["workflow"])
    assert "intent" in cls
    assert wf["persona"] == "architect"
    assert wf["execution_mode"] == "sequential"
