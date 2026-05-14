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
    """Phase 12: 'parallel' is implemented; use a genuinely-unknown mode."""
    runner = WorkflowRunner({})
    wf = Workflow(
        persona="architect", model="m", skill_name=None,
        execution_mode="phantom-mode", agents=("architect",),
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


# --- Phase 11d: Repair single-retry ---


class StubRepair:
    name = "repair"
    role = "stub"
    default_model = ""
    composer = False

    def __init__(self, plan: dict | None):
        self._plan = plan
        self.calls: list[tuple[str, AgentResult]] = []

    def run(self, input, context):
        return AgentResult(text="", ok=True, model=None, tokens_in=0, tokens_out=0, latency_ms=0)

    def plan_retry(self, agent_name, original_result, input):
        self.calls.append((agent_name, original_result))
        return self._plan


class FlakyAgent:
    name = "architect"
    role = "flaky"
    default_model = "primary-model"
    composer = True

    def __init__(self, *, fail_first: bool = True):
        self.calls: list[dict] = []
        self._fail_first = fail_first

    def run(self, input, context):
        self.calls.append({"override_model": input.metadata.get("override_model")})
        if self._fail_first and len(self.calls) == 1:
            return AgentResult(
                text="", ok=False, model=self.default_model,
                tokens_in=0, tokens_out=0, latency_ms=1,
                error="persona_llm_error",
            )
        return AgentResult(
            text="recovered response", ok=True,
            model=input.metadata.get("override_model") or self.default_model,
            tokens_in=10, tokens_out=20, latency_ms=2,
        )


def test_repair_retries_failing_agent_once():
    agent = FlakyAgent(fail_first=True)
    repair = StubRepair(plan={"model": "fallback-model"})
    runner = WorkflowRunner({"architect": agent, "repair": repair})
    wf_run_id = _seed_workflow_run()
    result = runner.execute(_wf(("architect",)), _ctx(1), "hi", workflow_run_id=wf_run_id)
    assert result.ok is True
    assert result.text == "recovered response"
    assert len(agent.calls) == 2
    assert agent.calls[0]["override_model"] is None
    assert agent.calls[1]["override_model"] == "fallback-model"
    rows = store.connection().execute(
        "SELECT outcome, retried FROM agent_runs WHERE workflow_run_id = ? ORDER BY id",
        (wf_run_id,),
    ).fetchall()
    assert [(r["outcome"], r["retried"]) for r in rows] == [("failure", 0), ("success", 1)]


def test_repair_gives_up_after_second_failure():
    class AlwaysFail(FlakyAgent):
        def run(self, input, context):
            self.calls.append({"override_model": input.metadata.get("override_model")})
            return AgentResult(
                text="", ok=False, model=self.default_model,
                tokens_in=0, tokens_out=0, latency_ms=1,
                error="persona_llm_error",
            )

    agent = AlwaysFail()
    repair = StubRepair(plan={"model": "fallback-model"})
    runner = WorkflowRunner({"architect": agent, "repair": repair})
    wf_run_id = _seed_workflow_run()
    result = runner.execute(_wf(("architect",)), _ctx(1), "hi", workflow_run_id=wf_run_id)
    assert result.ok is False
    assert "Sorry" in result.text
    assert len(agent.calls) == 2
    rows = store.connection().execute(
        "SELECT outcome, retried FROM agent_runs WHERE workflow_run_id = ? ORDER BY id",
        (wf_run_id,),
    ).fetchall()
    assert [(r["outcome"], r["retried"]) for r in rows] == [("failure", 0), ("failure", 1)]


def test_repair_skipped_when_plan_returns_none():
    agent = FlakyAgent(fail_first=True)
    repair = StubRepair(plan=None)
    runner = WorkflowRunner({"architect": agent, "repair": repair})
    wf_run_id = _seed_workflow_run()
    result = runner.execute(_wf(("architect",)), _ctx(1), "hi", workflow_run_id=wf_run_id)
    assert result.ok is False
    assert len(agent.calls) == 1
    assert len(repair.calls) == 1


def test_repair_agent_in_workflow_agents_list_is_skipped():
    """Listing 'repair' inside workflow.agents is a defensive no-op: Repair
    is consulted via plan_retry, not run as a workflow step."""
    agent = FlakyAgent(fail_first=False)
    repair = StubRepair(plan={"model": "x"})
    runner = WorkflowRunner({"architect": agent, "repair": repair})
    result = runner.execute(_wf(("repair", "architect")), _ctx(None), "hi")
    assert result.ok is True
    assert result.text == "recovered response"
    assert repair.calls == []


# --- Phase 12a: Parallel mode ---


def _wf_parallel(agents: tuple[str, ...]) -> Workflow:
    return Workflow(
        persona="architect", model="fake-model", skill_name=None,
        execution_mode="parallel", agents=agents,
    )


class SlowFakeAgent(FakeAgent):
    """Sleeps `delay_ms` before returning. Used for latency assertions."""
    def __init__(self, name: str, *, delay_ms: int, text: str = "ok"):
        super().__init__(name, text=text)
        self._delay_ms = delay_ms

    def run(self, input, context):
        import time as _t
        _t.sleep(self._delay_ms / 1000.0)
        return super().run(input, context)


def _composer_agent(name: str, *, text: str = "composed", ok: bool = True) -> FakeAgent:
    """A composer-flagged FakeAgent (composer=True via attribute)."""
    a = FakeAgent(name, text=text, ok=ok)
    a.composer = True
    return a


def test_parallel_two_agents_both_succeed():
    a = FakeAgent("research", text="findings A")
    b = _composer_agent("architect", text="response B")
    runner = WorkflowRunner({"research": a, "architect": b})
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(_wf_parallel(("research", "architect")), _ctx(conv_id), "hi")
    assert result.ok is True
    assert result.text == "response B"  # last-composer-wins
    assert len(a.calls) == 1 and len(b.calls) == 1


def test_parallel_latency_is_max_not_sum():
    """Two slow agents in parallel run in roughly max(delay_a, delay_b),
    not sum. Generous tolerance to avoid CI flakes."""
    a = SlowFakeAgent("research", delay_ms=200)
    b = SlowFakeAgent("architect", delay_ms=200)
    runner = WorkflowRunner({"research": a, "architect": b})
    conv_id = store.current_or_new_conversation("architect")
    import time as _t
    t0 = _t.monotonic()
    runner.execute(_wf_parallel(("research", "architect")), _ctx(conv_id), "hi")
    elapsed_ms = (_t.monotonic() - t0) * 1000
    assert elapsed_ms < 350, f"parallel took {elapsed_ms}ms; expected < 350 (max+overhead)"


def test_parallel_one_failing_keeps_composer_text_with_ok_true():
    """Mirrors sequential semantics: if the composer succeeded, the workflow's
    response is valid even when an upstream agent failed. The failing agent's
    row is still persisted in agent_runs."""
    a = FakeAgent("research", ok=False, text="", error="boom")
    b = _composer_agent("architect", text="ok")
    runner = WorkflowRunner({"research": a, "architect": b})
    wf_run_id = _seed_workflow_run()
    result = runner.execute(_wf_parallel(("research", "architect")), _ctx(1), "hi", workflow_run_id=wf_run_id)
    assert result.ok is True
    assert result.text == "ok"
    rows = store.connection().execute(
        "SELECT agent, outcome FROM agent_runs WHERE workflow_run_id = ? ORDER BY agent",
        (wf_run_id,),
    ).fetchall()
    assert {(r["agent"], r["outcome"]) for r in rows} == {
        ("architect", "success"),
        ("research", "failure"),
    }


def test_parallel_all_failing_returns_failure_result():
    """When NO agent succeeds, WorkflowResult.ok=False and text=LLM_FAILURE_MESSAGE."""
    a = FakeAgent("research", ok=False, text="", error="boom1")
    b = FakeAgent("architect", ok=False, text="", error="boom2")
    runner = WorkflowRunner({"research": a, "architect": b})
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(_wf_parallel(("research", "architect")), _ctx(conv_id), "hi")
    assert result.ok is False
    assert "Sorry" in result.text


def test_parallel_does_not_retry_on_failure():
    """Sequential mode consults RepairAgent on agent_failed; parallel does not.
    Cancel-and-retry semantics in fan-out are ambiguous; Phase 13 may revisit."""
    agent = FlakyAgent(fail_first=True)
    repair = StubRepair(plan={"model": "fallback"})
    runner = WorkflowRunner({"architect": agent, "repair": repair})
    conv_id = store.current_or_new_conversation("architect")
    runner.execute(_wf_parallel(("architect",)), _ctx(conv_id), "hi")
    # Only one call, never retried.
    assert len(agent.calls) == 1
    assert repair.calls == []  # Repair was not consulted in parallel mode


def test_parallel_agents_see_no_prior_findings():
    """Parallel mode does NOT thread findings; every agent sees prior_findings=()."""
    a = FakeAgent("research", text="findings A")
    b = FakeAgent("architect", text="response B")
    runner = WorkflowRunner({"research": a, "architect": b})
    conv_id = store.current_or_new_conversation("architect")
    runner.execute(_wf_parallel(("research", "architect")), _ctx(conv_id), "hi")
    assert a.calls[0].prior_findings == ()
    assert b.calls[0].prior_findings == ()


def test_parallel_composer_pick_uses_workflow_order_not_completion_order():
    """Even if a non-composer finishes after the composer, last-composer-wins
    is decided by the agent's INDEX in workflow.agents (deterministic)."""
    a = _composer_agent("architect", text="first composer")
    b = _composer_agent("coding", text="second composer")
    runner = WorkflowRunner({"architect": a, "coding": b})
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(_wf_parallel(("architect", "coding")), _ctx(conv_id), "hi")
    # workflow.agents = ("architect", "coding") -> coding is last in the list
    assert result.text == "second composer"


def test_parallel_writes_one_agent_runs_row_per_agent():
    a = FakeAgent("research")
    b = _composer_agent("architect")
    runner = WorkflowRunner({"research": a, "architect": b})
    wf_run_id = _seed_workflow_run()
    runner.execute(_wf_parallel(("research", "architect")), _ctx(1), "hi", workflow_run_id=wf_run_id)
    rows = store.connection().execute(
        "SELECT agent, outcome FROM agent_runs WHERE workflow_run_id = ? ORDER BY agent",
        (wf_run_id,),
    ).fetchall()
    assert {(r["agent"], r["outcome"]) for r in rows} == {
        ("architect", "success"),
        ("research", "success"),
    }


# --- Phase 12b: Competitive mode ---


def _wf_competitive(agents: tuple[str, ...]) -> Workflow:
    return Workflow(
        persona="architect", model="fake-model", skill_name=None,
        execution_mode="competitive", agents=agents,
    )


class FakeEvaluator:
    """Minimum surface for competitive: rank()."""
    name = "evaluator"
    role = "fake judge"
    default_model = "test-eval"
    composer = False

    def __init__(self, *, ranking: dict | None):
        self._ranking = ranking
        self.calls: list[dict] = []

    def rank(self, message, candidates, override_model=None):
        self.calls.append({"message": message, "candidates": candidates})
        return self._ranking


def test_competitive_picks_winner_via_rank():
    a = _composer_agent("coding", text="def f(): pass")
    b = _composer_agent("architect", text="here's a longer explanation...")
    evaluator = FakeEvaluator(ranking={
        "winner": "architect", "winner_index": 1,
        "reason": "more complete",
        "scores": [{"index": 0, "score": 0.7, "note": "ok"},
                   {"index": 1, "score": 0.9, "note": "complete"}],
    })
    runner = WorkflowRunner({"coding": a, "architect": b, "evaluator": evaluator})
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(_wf_competitive(("coding", "architect", "evaluator")), _ctx(conv_id), "write a fn")
    assert result.ok is True
    assert result.text == "here's a longer explanation..."
    assert result.evaluator_confidence == 0.9
    assert len(evaluator.calls) == 1
    assert [n for n, _ in evaluator.calls[0]["candidates"]] == ["coding", "architect"]


def test_competitive_requires_evaluator_as_last_agent():
    a = _composer_agent("coding")
    b = _composer_agent("architect")
    runner = WorkflowRunner({"coding": a, "architect": b})
    conv_id = store.current_or_new_conversation("architect")
    with pytest.raises(ValueError, match="evaluator"):
        runner.execute(_wf_competitive(("coding", "architect")), _ctx(conv_id), "x")


def test_competitive_falls_back_to_first_ok_when_rank_returns_none():
    a = _composer_agent("coding", text="A text")
    b = _composer_agent("architect", text="B text")
    evaluator = FakeEvaluator(ranking=None)  # parse error path
    runner = WorkflowRunner({"coding": a, "architect": b, "evaluator": evaluator})
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(_wf_competitive(("coding", "architect", "evaluator")), _ctx(conv_id), "x")
    assert result.ok is True
    assert result.text == "A text"  # first ok candidate


def test_competitive_writes_evaluator_agent_runs_row():
    a = _composer_agent("coding", text="A text")
    b = _composer_agent("architect", text="B text")
    evaluator = FakeEvaluator(ranking={
        "winner": "coding", "winner_index": 0,
        "reason": "x",
        "scores": [{"index": 0, "score": 0.8, "note": "ok"}],
    })
    runner = WorkflowRunner({"coding": a, "architect": b, "evaluator": evaluator})
    wf_run_id = _seed_workflow_run()
    runner.execute(_wf_competitive(("coding", "architect", "evaluator")), _ctx(1), "x",
                   workflow_run_id=wf_run_id)
    rows = store.connection().execute(
        "SELECT agent, confidence, outcome FROM agent_runs WHERE workflow_run_id = ? ORDER BY id",
        (wf_run_id,),
    ).fetchall()
    agent_names = [r["agent"] for r in rows]
    assert agent_names == ["coding", "architect", "evaluator"]
    eval_row = rows[-1]
    assert eval_row["confidence"] == 0.8
    assert eval_row["outcome"] == "success"


def test_competitive_all_competitors_failing_returns_failure():
    a = FakeAgent("coding", ok=False, text="", error="boom1")
    b = FakeAgent("architect", ok=False, text="", error="boom2")
    evaluator = FakeEvaluator(ranking=None)
    runner = WorkflowRunner({"coding": a, "architect": b, "evaluator": evaluator})
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(_wf_competitive(("coding", "architect", "evaluator")), _ctx(conv_id), "x")
    assert result.ok is False
    assert evaluator.calls == []  # rank never called when no ok candidates


# --- Phase 12c: Collaborative mode ---


def _wf_collab(agents: tuple[str, ...]) -> Workflow:
    return Workflow(
        persona="architect", model="fake-model", skill_name=None,
        execution_mode="collaborative", agents=agents,
    )


def test_collaborative_merges_under_role_headings():
    a = FakeAgent("research", text="Postgres has X.")
    a.role = "retrieval and synthesis"
    b = FakeAgent("critic", text="X has risk Y.")
    b.role = "contrarian challenger"
    runner = WorkflowRunner({"research": a, "critic": b})
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(_wf_collab(("research", "critic")), _ctx(conv_id), "brief on X")
    assert result.ok is True
    # Headings under "## <role>" in workflow.agents order.
    assert result.text.startswith("## retrieval and synthesis")
    assert "## contrarian challenger" in result.text
    assert "Postgres has X." in result.text
    assert "X has risk Y." in result.text
    # Model carries the strategy marker.
    assert result.model == "collaborative"


def test_collaborative_drops_failing_section_keeps_others():
    a = FakeAgent("research", text="facts")
    a.role = "retrieval and synthesis"
    b = FakeAgent("critic", ok=False, text="", error="boom")
    b.role = "contrarian challenger"
    runner = WorkflowRunner({"research": a, "critic": b})
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(_wf_collab(("research", "critic")), _ctx(conv_id), "x")
    assert result.ok is True  # at least one producer ok
    assert "retrieval and synthesis" in result.text
    assert "contrarian challenger" not in result.text  # failed; section dropped


def test_collaborative_all_failing_returns_failure_message():
    a = FakeAgent("research", ok=False, text="", error="boom1")
    b = FakeAgent("critic", ok=False, text="", error="boom2")
    runner = WorkflowRunner({"research": a, "critic": b})
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(_wf_collab(("research", "critic")), _ctx(conv_id), "x")
    assert result.ok is False
    assert "Sorry" in result.text


def test_debate_full_2_rounds_plus_synthesis():
    """5 agent_runs rows expected: A, B, A, B, synth. Each turn shows the
    correct debate_role and an increasing transcript."""

    seen_metadata: list[dict] = []

    class TurnCountingAgent(FakeAgent):
        """Returns a different text on each call so synth's output is
        distinguishable from the debater's first turn."""
        composer = True

        def __init__(self, name: str):
            super().__init__(name, text="")
            self._call_no = 0

        def run(self, input, context):
            self._call_no += 1
            seen_metadata.append({
                "agent": self.name,
                "debate_role": input.metadata.get("debate_role"),
                "prior_findings_len": len(input.prior_findings),
                "call_no": self._call_no,
            })
            return AgentResult(
                text=f"{self.name} turn #{self._call_no}",
                ok=True, model=self.default_model,
                tokens_in=2, tokens_out=3, latency_ms=1,
            )

    a = TurnCountingAgent("architect")
    b = TurnCountingAgent("operator")
    runner = WorkflowRunner({"architect": a, "operator": b})
    wf = Workflow(
        persona="architect", model="m", skill_name=None,
        execution_mode="debate", agents=("architect", "operator", "architect"),
        rounds=2,
    )
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(wf, _ctx(conv_id), "should we adopt microservices?")
    assert result.ok is True
    # Synth is the LAST architect call (call_no=3 for architect; total 5 dispatches).
    assert result.text == "architect turn #3"
    assert len(seen_metadata) == 5
    # First A turn has no challenge role; subsequent debater turns do; synth tagged.
    assert seen_metadata[0]["debate_role"] is None
    assert seen_metadata[1]["debate_role"] == "challenge"
    assert seen_metadata[2]["debate_role"] == "challenge"
    assert seen_metadata[3]["debate_role"] == "challenge"
    assert seen_metadata[4]["debate_role"] == "synthesize"
    # Transcript grows: dispatch 0 sees nothing, dispatch 4 sees all 4 prior.
    assert seen_metadata[1]["prior_findings_len"] == 1
    assert seen_metadata[3]["prior_findings_len"] == 3
    assert seen_metadata[4]["prior_findings_len"] == 4


def test_debate_rounds_1():
    """3 rows: A, B, synth."""
    a = _composer_agent("architect", text="A says X")
    b = _composer_agent("operator", text="B says Y")
    runner = WorkflowRunner({"architect": a, "operator": b})
    wf = Workflow(
        persona="architect", model="m", skill_name=None,
        execution_mode="debate", agents=("architect", "operator", "architect"),
        rounds=1,
    )
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(wf, _ctx(conv_id), "q")
    assert result.ok is True
    # FakeAgent (architect) is reused for synth; same text returned for synth.
    # 3 total dispatches: a (round 1), b (round 1), a (synth).
    assert len(a.calls) == 2  # architect spoke twice (round 1 + synth)
    assert len(b.calls) == 1


def test_debate_requires_at_least_3_agents():
    a = _composer_agent("architect")
    b = _composer_agent("operator")
    runner = WorkflowRunner({"architect": a, "operator": b})
    wf = Workflow(
        persona="architect", model="m", skill_name=None,
        execution_mode="debate", agents=("architect", "operator"),
        rounds=2,
    )
    conv_id = store.current_or_new_conversation("architect")
    with pytest.raises(ValueError, match="3 entries"):
        runner.execute(wf, _ctx(conv_id), "q")


def test_debate_short_circuits_on_debater_failure_synth_still_runs():
    """If a debater fails mid-round, the runner stops the debate loop but
    still calls the synthesizer with whatever transcript exists."""
    a = _composer_agent("architect", text="A's only turn")
    b = FakeAgent("operator", ok=False, text="", error="boom")
    runner = WorkflowRunner({"architect": a, "operator": b})
    wf = Workflow(
        persona="architect", model="m", skill_name=None,
        execution_mode="debate", agents=("architect", "operator", "architect"),
        rounds=2,
    )
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(wf, _ctx(conv_id), "q")
    # Synthesizer (architect, reused) ran with the truncated transcript.
    assert result.ok is True
    # architect spoke twice: round 1 + synth. b failed once.
    assert len(a.calls) == 2
    assert len(b.calls) == 1


def test_debate_collaborative_post_step_runs_trailing_evaluator():
    """Renamed-position anchor: keep collaborative test grouped after debate."""

# --- Phase 12e: Speculative mode ---


class FakeAgreeingEvaluator:
    """Minimal surface for speculative: agree()."""
    name = "evaluator"
    role = "fake judge"
    default_model = "test-eval"
    composer = False

    def __init__(self, *, agree_value: bool | None):
        self._agree = agree_value
        self.calls: list[dict] = []

    def agree(self, message, text_a, text_b, override_model=None):
        self.calls.append({"message": message, "text_a": text_a, "text_b": text_b})
        return self._agree


def _wf_speculative(agents: tuple[str, ...], *, timeout_s: int | None = None) -> Workflow:
    return Workflow(
        persona="architect", model="fake-model", skill_name=None,
        execution_mode="speculative", agents=agents, timeout_s=timeout_s,
    )


def test_speculative_both_ok_agree_returns_cheap_only():
    cheap = FakeAgent("casual", text="quick answer")
    strong = FakeAgent("architect", text="thorough answer")
    evaluator = FakeAgreeingEvaluator(agree_value=True)
    runner = WorkflowRunner({"casual": cheap, "architect": strong, "evaluator": evaluator})
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(_wf_speculative(("casual", "architect", "evaluator")), _ctx(conv_id), "q")
    assert result.ok is True
    assert result.text == "quick answer"
    assert "Correction" not in result.text
    assert len(evaluator.calls) == 1


def test_speculative_both_ok_disagree_appends_correction():
    cheap = FakeAgent("casual", text="quick answer")
    strong = FakeAgent("architect", text="actually different answer")
    evaluator = FakeAgreeingEvaluator(agree_value=False)
    runner = WorkflowRunner({"casual": cheap, "architect": strong, "evaluator": evaluator})
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(_wf_speculative(("casual", "architect", "evaluator")), _ctx(conv_id), "q")
    assert result.ok is True
    assert result.text.startswith("quick answer")
    assert "[Correction (slower model):]" in result.text
    assert "actually different answer" in result.text


def test_speculative_cheap_ok_strong_fails_no_correction():
    cheap = FakeAgent("casual", text="quick answer")
    strong = FakeAgent("architect", ok=False, text="", error="boom")
    evaluator = FakeAgreeingEvaluator(agree_value=False)  # would otherwise correct
    runner = WorkflowRunner({"casual": cheap, "architect": strong, "evaluator": evaluator})
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(_wf_speculative(("casual", "architect", "evaluator")), _ctx(conv_id), "q")
    assert result.ok is True
    assert result.text == "quick answer"
    assert "Correction" not in result.text
    # agree() should NOT have been called when strong failed
    assert evaluator.calls == []


def test_speculative_cheap_fails_strong_ok_uses_strong_no_correction():
    cheap = FakeAgent("casual", ok=False, text="", error="boom")
    strong = FakeAgent("architect", text="thorough answer")
    evaluator = FakeAgreeingEvaluator(agree_value=False)
    runner = WorkflowRunner({"casual": cheap, "architect": strong, "evaluator": evaluator})
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(_wf_speculative(("casual", "architect", "evaluator")), _ctx(conv_id), "q")
    assert result.ok is True
    assert result.text == "thorough answer"
    # No correction since base IS strong; the "cheap was wrong, here's the correction"
    # framing only makes sense when cheap succeeded but disagreed.
    assert "Correction" not in result.text
    assert evaluator.calls == []


def test_speculative_both_failing_returns_failure_message():
    cheap = FakeAgent("casual", ok=False, text="", error="boom1")
    strong = FakeAgent("architect", ok=False, text="", error="boom2")
    evaluator = FakeAgreeingEvaluator(agree_value=True)
    runner = WorkflowRunner({"casual": cheap, "architect": strong, "evaluator": evaluator})
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(_wf_speculative(("casual", "architect", "evaluator")), _ctx(conv_id), "q")
    assert result.ok is False
    assert "Sorry" in result.text


def test_speculative_no_evaluator_skips_agreement_check():
    """Workflow without trailing evaluator: no agreement check, just cheap-or-strong."""
    cheap = FakeAgent("casual", text="quick")
    strong = FakeAgent("architect", text="thorough")
    runner = WorkflowRunner({"casual": cheap, "architect": strong})
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(_wf_speculative(("casual", "architect")), _ctx(conv_id), "q")
    assert result.ok is True
    assert result.text == "quick"


def test_speculative_requires_at_least_2_agents():
    runner = WorkflowRunner({})
    wf = Workflow(
        persona="architect", model="m", skill_name=None,
        execution_mode="speculative", agents=("casual",),
    )
    conv_id = store.current_or_new_conversation("architect")
    with pytest.raises(ValueError, match="cheap, strong"):
        runner.execute(wf, _ctx(conv_id), "q")


def test_collaborative_runs_trailing_evaluator_sequentially_after_merge():
    """Phase 10 evaluate-flag interaction: evaluator runs AFTER the parallel
    section, sees the merged document, scores it."""
    a = FakeAgent("research", text="facts")
    a.role = "retrieval and synthesis"
    b = FakeAgent("critic", text="risks")
    b.role = "contrarian challenger"
    seen_findings: list[tuple] = []

    class CapturingEvaluator(FakeAgent):
        composer = False

        def run(self, input, context):
            seen_findings.append(input.prior_findings)
            return AgentResult(
                text="conf 0.8", ok=True, model="m",
                tokens_in=0, tokens_out=0, latency_ms=1, confidence=0.8,
            )

    evaluator = CapturingEvaluator("evaluator")
    runner = WorkflowRunner({"research": a, "critic": b, "evaluator": evaluator})
    conv_id = store.current_or_new_conversation("architect")
    result = runner.execute(_wf_collab(("research", "critic", "evaluator")), _ctx(conv_id), "x")
    assert result.ok is True
    # Evaluator saw the merged document as its sole prior finding.
    assert len(seen_findings) == 1 and len(seen_findings[0]) == 1
    merged_seen = seen_findings[0][0]
    assert "## retrieval and synthesis" in merged_seen
    assert "## contrarian challenger" in merged_seen
    assert result.evaluator_confidence == 0.8
