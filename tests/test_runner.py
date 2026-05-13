from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context, events, skills  # noqa: E402
from ubongo.agents.base import AgentInput, AgentResult  # noqa: E402
from ubongo.master import Context, Workflow  # noqa: E402
from ubongo.memory import store, vault  # noqa: E402
from ubongo.runner import WorkflowRunner  # noqa: E402


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


class FakeAgent:
    def __init__(self, name: str, *, text: str = "ok", ok: bool = True, error: str | None = None,
                 raises: Exception | None = None):
        self.name = name
        self.role = f"{name} role"
        self.default_model = "fake-model"
        self._text = text
        self._ok = ok
        self._error = error
        self._raises = raises
        self.calls: list[AgentInput] = []

    def run(self, input, context):
        self.calls.append(input)
        if self._raises is not None:
            raise self._raises
        return AgentResult(
            text=self._text, ok=self._ok, model=self.default_model,
            tokens_in=2, tokens_out=3, latency_ms=1,
            error=self._error,
        )


def _ctx(conv_id: int | None) -> Context:
    return Context(conversation_id=conv_id, persona="architect", auto_mode=False, pending_skill=None)


def _wf(agents: tuple[str, ...]) -> Workflow:
    return Workflow(
        persona="architect", model="fake-model", skill_name=None,
        execution_mode="sequential", agents=agents,
    )


def _seed_workflow_run() -> int:
    conv_id = store.current_or_new_conversation("architect")
    msg_id = store.append_message(conv_id, "user", "hi", persona="architect")
    return store.append_workflow_run(
        conversation_id=conv_id,
        message_id=msg_id,
        classification={"intent": "technical"},
        workflow={"persona": "architect", "agents": ["architect"]},
        execution_mode="sequential",
        outcome="success",
        started_at=store.now_iso(),
    )


def test_single_agent_workflow_returns_that_agents_text():
    agent = FakeAgent("architect", text="hello")
    runner = WorkflowRunner({"architect": agent})
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(_wf(("architect",)), _ctx(conv_id), "hi")
    assert result.ok is True
    assert result.text == "hello"
    assert len(agent.calls) == 1


def test_sequential_dispatch_threads_findings():
    a = FakeAgent("research", text="findings A")
    b = FakeAgent("architect", text="response B")
    runner = WorkflowRunner({"research": a, "architect": b})
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(_wf(("research", "architect")), _ctx(conv_id), "hi")
    assert result.text == "response B"
    # second agent saw the first's findings
    assert b.calls[0].prior_findings == ("findings A",)
    assert a.calls[0].prior_findings == ()


def test_agent_runs_rows_written_when_workflow_run_id_provided():
    a = FakeAgent("research")
    b = FakeAgent("architect")
    runner = WorkflowRunner({"research": a, "architect": b})
    wf_run_id = _seed_workflow_run()
    runner.execute(_wf(("research", "architect")), _ctx(1), "hi", workflow_run_id=wf_run_id)
    rows = store.connection().execute(
        "SELECT agent, outcome FROM agent_runs WHERE workflow_run_id = ? ORDER BY id",
        (wf_run_id,),
    ).fetchall()
    assert [r["agent"] for r in rows] == ["research", "architect"]
    assert all(r["outcome"] == "success" for r in rows)


def test_agent_started_and_completed_events_dispatched_in_order():
    a = FakeAgent("research")
    b = FakeAgent("architect")
    runner = WorkflowRunner({"research": a, "architect": b})
    seen: list[str] = []
    events.register("agent_started", lambda p: seen.append(f"start:{p['agent']}"))
    events.register("agent_completed", lambda p: seen.append(f"done:{p['agent']}"))
    conv_id = store.current_or_new_conversation("architect")
    runner.execute(_wf(("research", "architect")), _ctx(conv_id), "hi")
    assert seen == ["start:research", "done:research", "start:architect", "done:architect"]


def test_agent_failed_dispatched_on_ok_false_and_runner_continues():
    a = FakeAgent("research", ok=False, text="", error="boom")
    b = FakeAgent("architect", text="response B")
    runner = WorkflowRunner({"research": a, "architect": b})
    seen: list[dict] = []
    events.register("agent_failed", lambda p: seen.append(p))
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(_wf(("research", "architect")), _ctx(conv_id), "hi")
    assert len(seen) == 1
    assert seen[0]["agent"] == "research"
    assert result.text == "response B"
    # research failure doesn't poison persona's prior_findings
    assert b.calls[0].prior_findings == ()


def test_unknown_execution_mode_raises():
    runner = WorkflowRunner({})
    wf = Workflow(
        persona="architect", model="m", skill_name=None,
        execution_mode="parallel", agents=("architect",),
    )
    conv_id = store.current_or_new_conversation("architect")
    with pytest.raises(NotImplementedError):
        runner.execute(wf, _ctx(conv_id), "hi")


def test_all_agents_fail_returns_failure_result():
    a = FakeAgent("research", ok=False, text="", error="boom1")
    b = FakeAgent("architect", ok=False, text="", error="boom2")
    runner = WorkflowRunner({"research": a, "architect": b})
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(_wf(("research", "architect")), _ctx(conv_id), "hi")
    assert result.ok is False
    assert "Sorry, I couldn't reach the model" in result.text


def test_agent_exception_recorded_as_failure_with_typename_error():
    a = FakeAgent("research", raises=ValueError("nope"))
    b = FakeAgent("architect", text="response B")
    runner = WorkflowRunner({"research": a, "architect": b})
    wf_run_id = _seed_workflow_run()
    result = runner.execute(_wf(("research", "architect")), _ctx(1), "hi", workflow_run_id=wf_run_id)
    assert result.text == "response B"
    rows = store.connection().execute(
        "SELECT agent, outcome FROM agent_runs WHERE workflow_run_id = ? ORDER BY id",
        (wf_run_id,),
    ).fetchall()
    assert rows[0]["agent"] == "research"
    assert rows[0]["outcome"] == "failure"


# --- Code-review regression test (2026-05-13) ---


def test_history_contains_user_message_exactly_once(tmp_path):
    """Regression for review finding #2: master writes the user message to the
    store, then the runner builds history via store.recall (which includes it)
    AND used to append current_message a second time. Result: every turn sent
    the user message twice to the LLM."""
    from ubongo.runner import build_message_history

    conv_id = store.current_or_new_conversation("casual")
    store.append_message(conv_id, "user", "hello world", persona="casual")
    summary, hist = build_message_history(conv_id, "hello world")
    user_lines = [m for m in hist if m["role"] == "user" and m["content"] == "hello world"]
    assert len(user_lines) == 1


def test_history_no_conv_id_still_includes_message():
    """Edge case: when conv_id is None (no persisted history), the runner must
    still surface the current user message as the single user turn."""
    from ubongo.runner import build_message_history

    summary, hist = build_message_history(None, "hi there")
    assert summary is None
    assert hist == [{"role": "user", "content": "hi there"}]
