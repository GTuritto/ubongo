from __future__ import annotations

import os
import pstats
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context, events, profiling, skills  # noqa: E402
from ubongo.commands import ReplState  # noqa: E402
from ubongo.memory import store, vault  # noqa: E402
from ubongo.repl import _cmd_profile, _parse_profile_command  # noqa: E402


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


def _state() -> ReplState:
    return ReplState(
        persona="architect", auto_mode=False,
        pending_skill=None, pending_workflow=None,
    )


def _seed_workflow(
    *,
    mode: str = "sequential",
    outcome: str = "success",
    started_at: str = "2026-06-11T10:00:00.000Z",
    ended_at: str | None = "2026-06-11T10:00:02.000Z",
    agents: tuple[dict, ...] = (),
) -> int:
    conv = store.current_or_new_conversation("architect")
    msg = store.append_message(conv, "user", "q", persona="architect")
    wf = store.append_workflow_run(
        conversation_id=conv, message_id=msg,
        classification={"intent": "technical", "confidence": 0.8},
        workflow={"persona": "architect", "execution_mode": mode, "agents": []},
        execution_mode=mode, outcome=outcome,
        started_at=started_at, ended_at=ended_at,
    )
    for spec in agents:
        store.append_agent_run(
            workflow_run_id=wf,
            agent=spec.get("agent", "research"),
            model=spec.get("model", "m1"),
            input={}, output={}, confidence=None,
            tokens_in=spec.get("tokens_in", 10),
            tokens_out=spec.get("tokens_out", 20),
            latency_ms=spec.get("latency_ms", 100),
            outcome=spec.get("outcome", "success"),
            started_at=started_at, ended_at=ended_at or started_at,
            retried=spec.get("retried", False),
        )
    return wf


# ---------- p95 ----------


def test_p95_empty_is_none():
    assert profiling._p95([]) is None


def test_p95_single_value():
    assert profiling._p95([42.0]) == 42.0


def test_p95_nearest_rank():
    # 1..100: ceil(0.95*100)=95 -> the 95th smallest
    assert profiling._p95([float(v) for v in range(1, 101)]) == 95.0


# ---------- summary ----------


def test_summary_empty_db_returns_none():
    assert profiling.summary() is None
    assert profiling.render_summary() == "No runs recorded yet."


def test_summary_counts_tokens_and_latency():
    _seed_workflow(agents=(
        {"agent": "research", "latency_ms": 1500, "tokens_in": 100, "tokens_out": 50},
        {"agent": "evaluator", "latency_ms": 300, "tokens_in": 40, "tokens_out": 10},
    ))
    s = profiling.summary()
    assert s.turns == 1
    assert s.avg_latency_ms == pytest.approx(2000.0)  # 10:00:00 -> 10:00:02
    assert s.p95_latency_ms == pytest.approx(2000.0)
    assert s.tokens_in == 140
    assert s.tokens_out == 60
    assert s.slowest_agent == "research"
    assert s.slowest_agent_ms == 1500


def test_summary_skips_workflows_without_ended_at():
    _seed_workflow(ended_at=None, outcome="in_progress")
    s = profiling.summary()
    assert s.turns == 1
    assert s.avg_latency_ms is None
    assert s.p95_latency_ms is None
    assert "—" in profiling.render_summary()


# ---------- breakdowns ----------


def test_by_agent_groups_failures_and_retries():
    _seed_workflow(agents=(
        {"agent": "coding", "latency_ms": 400, "outcome": "success"},
        {"agent": "coding", "latency_ms": 600, "outcome": "failure"},
        {"agent": "coding", "latency_ms": 500, "outcome": "success", "retried": True},
        {"agent": "critic", "latency_ms": 100},
    ))
    groups = {g.key: g for g in profiling.by_agent()}
    coding = groups["coding"]
    assert coding.runs == 3
    assert coding.avg_latency_ms == pytest.approx(500.0)
    assert coding.failures == 1
    assert coding.failure_pct == pytest.approx(100.0 / 3)
    assert coding.retried == 1
    assert groups["critic"].runs == 1


def test_by_agent_sorted_by_total_latency_desc():
    _seed_workflow(agents=(
        {"agent": "fast", "latency_ms": 10},
        {"agent": "slow", "latency_ms": 9000},
    ))
    assert [g.key for g in profiling.by_agent()] == ["slow", "fast"]


def test_by_model_groups_null_model_as_dash():
    _seed_workflow(agents=(
        {"agent": "a", "model": "anthropic/x"},
        {"agent": "b", "model": None},
    ))
    keys = {g.key for g in profiling.by_model()}
    assert keys == {"anthropic/x", "—"}


def test_by_mode_uses_workflow_latency_and_outcomes():
    _seed_workflow(mode="sequential", outcome="success")
    _seed_workflow(mode="sequential", outcome="failure")
    _seed_workflow(mode="parallel", outcome="repaired")
    groups = {g.key: g for g in profiling.by_mode()}
    assert groups["sequential"].runs == 2
    assert groups["sequential"].failures == 1
    assert groups["parallel"].retried == 1  # 'repaired' workflows
    assert groups["sequential"].avg_latency_ms == pytest.approx(2000.0)


def test_last_n_filters_to_recent_workflows():
    _seed_workflow(agents=({"agent": "old", "latency_ms": 1},))
    _seed_workflow(agents=({"agent": "new", "latency_ms": 2},))
    keys = {g.key for g in profiling.by_agent(last_n=1)}
    assert keys == {"new"}
    assert profiling.summary(last_n=1).turns == 1


def test_render_breakdowns_empty_db():
    assert profiling.render_agents() == "No runs recorded yet."
    assert profiling.render_models() == "No runs recorded yet."
    assert profiling.render_modes() == "No runs recorded yet."


def test_render_agents_contains_columns():
    _seed_workflow(agents=({"agent": "research", "latency_ms": 120},))
    out = profiling.render_agents()
    assert "Per-agent profile (all runs):" in out
    assert "research" in out
    assert "fail%" in out


# ---------- /profile parser ----------


def test_parse_profile_default_summary():
    assert _parse_profile_command("/profile") == ("summary", None)


def test_parse_profile_summary_with_n():
    assert _parse_profile_command("/profile 5") == ("summary", 5)


def test_parse_profile_breakdowns_with_and_without_n():
    assert _parse_profile_command("/profile agents") == ("agents", None)
    assert _parse_profile_command("/profile models 3") == ("models", 3)
    assert _parse_profile_command("/profile modes 7") == ("modes", 7)


def test_parse_profile_cpu_actions():
    assert _parse_profile_command("/profile cpu on") == ("cpu", "on")
    assert _parse_profile_command("/profile cpu off") == ("cpu", "off")
    assert _parse_profile_command("/profile cpu status") == ("cpu", "status")


def test_parse_profile_rejects_garbage():
    assert _parse_profile_command("/profile foo") is None
    assert _parse_profile_command("/profile agents zero") is None
    assert _parse_profile_command("/profile 0") is None
    assert _parse_profile_command("/profile -1") is None
    assert _parse_profile_command("/profile cpu") is None
    assert _parse_profile_command("/profile cpu maybe") is None
    assert _parse_profile_command("/profile agents 2 3") is None


# ---------- /profile handler ----------


def test_cmd_profile_bad_args_returns_usage():
    out = _cmd_profile("/profile bogus", _state())
    assert out.startswith("Usage: /profile")


def test_cmd_profile_summary_empty_db():
    assert _cmd_profile("/profile", _state()) == "No runs recorded yet."


def test_cmd_profile_cpu_toggle_mutates_state():
    state = _state()
    assert state.cpu_profile is False
    on_msg = _cmd_profile("/profile cpu on", state)
    assert state.cpu_profile is True
    assert "CPU profiling on" in on_msg
    assert "profiles" in on_msg
    status = _cmd_profile("/profile cpu status", state)
    assert "on" in status
    off_msg = _cmd_profile("/profile cpu off", state)
    assert state.cpu_profile is False
    assert "off" in off_msg


# ---------- CPU profiling ----------


def test_profile_call_returns_result_and_writes_prof():
    result, report = profiling.profile_call(sorted, [3, 1, 2])
    assert result == [1, 2, 3]
    assert report is not None
    assert "CPU profile written to" in report
    profs = list(profiling.profiles_dir().glob("turn-*.prof"))
    assert len(profs) == 1
    # the dump is a valid pstats file
    pstats.Stats(str(profs[0]))


def test_profile_call_propagates_fn_exception_without_report():
    def boom():
        raise ValueError("turn failed")

    with pytest.raises(ValueError, match="turn failed"):
        profiling.profile_call(boom)
    assert not profiling.profiles_dir().exists()


def test_profile_call_swallows_report_failure(monkeypatch):
    def broken_dir():
        raise OSError("disk says no")

    monkeypatch.setattr(profiling, "profiles_dir", broken_dir)
    result, report = profiling.profile_call(sorted, [2, 1])
    assert result == [1, 2]
    assert report is None


def test_profile_call_same_second_collision_gets_suffix(monkeypatch):
    monkeypatch.setattr(profiling.time, "strftime", lambda fmt: "20260611-120000")
    profiling.profile_call(sorted, [1])
    profiling.profile_call(sorted, [1])
    names = sorted(p.name for p in profiling.profiles_dir().glob("*.prof"))
    assert names == ["turn-20260611-120000-2.prof", "turn-20260611-120000.prof"]


def test_profiles_dir_is_sibling_of_db(tmp_path: Path):
    assert profiling.profiles_dir() == store.get_db_path().parent / "profiles"


# ---------- memory profiling (candidate 11) ----------


@pytest.fixture(autouse=True)
def _mem_disarmed():
    yield
    profiling.mem_stop()


def test_parse_profile_mem_actions():
    assert _parse_profile_command("/profile mem") == ("mem", "report")
    assert _parse_profile_command("/profile mem on") == ("mem", "on")
    assert _parse_profile_command("/profile mem off") == ("mem", "off")
    assert _parse_profile_command("/profile mem status") == ("mem", "status")


def test_parse_profile_mem_rejects_garbage():
    assert _parse_profile_command("/profile mem maybe") is None
    assert _parse_profile_command("/profile mem on extra") is None


def test_mem_report_none_when_unarmed():
    assert profiling.mem_active() is False
    assert profiling.mem_report() is None


def test_mem_arm_report_detects_growth():
    profiling.mem_start()
    assert profiling.mem_active() is True
    hoard = [bytearray(1024) for _ in range(2000)]  # ~2 MiB allocated here
    report = profiling.mem_report()
    assert report is not None
    assert "Memory growth since baseline" in report
    assert "test_profiling.py" in report  # this file is the growth site
    del hoard


def test_mem_stop_clears_baseline_and_tracing():
    profiling.mem_start()
    profiling.mem_stop()
    assert profiling.mem_active() is False
    assert profiling.mem_report() is None
    import tracemalloc
    assert tracemalloc.is_tracing() is False


def test_mem_rearm_replaces_baseline():
    profiling.mem_start()
    hoard = [bytearray(1024) for _ in range(2000)]
    profiling.mem_start()  # re-arm: new baseline includes the hoard
    report = profiling.mem_report()
    assert report is not None
    # the hoard predates the new baseline, so no growth from this line
    assert "no allocation growth recorded" in report or "test_profiling.py" not in report
    del hoard


def test_mem_report_failure_swallowed(monkeypatch):
    profiling.mem_start()

    def boom(*args, **kwargs):
        raise RuntimeError("snapshot says no")

    monkeypatch.setattr(profiling.tracemalloc, "take_snapshot", boom)
    assert profiling.mem_report() == "Memory report failed; see logs."


def test_cmd_profile_mem_toggle_and_report():
    state = _state()
    on_msg = _cmd_profile("/profile mem on", state)
    assert "Memory profiling armed" in on_msg
    assert profiling.mem_active() is True
    assert "on" in _cmd_profile("/profile mem status", state)
    report = _cmd_profile("/profile mem", state)
    assert "Memory growth since baseline" in report
    off_msg = _cmd_profile("/profile mem off", state)
    assert "off" in off_msg
    assert profiling.mem_active() is False
    unarmed = _cmd_profile("/profile mem", state)
    assert "Memory profiling is off" in unarmed


# ---------- startup switch (candidate 12) ----------


def test_resolve_startup_profile_flag_wins_over_env():
    assert profiling.resolve_startup_profile("mem", "cpu") == "mem"
    assert profiling.resolve_startup_profile("off", "cpu") is None


def test_resolve_startup_profile_env_fallback():
    assert profiling.resolve_startup_profile(None, "cpu") == "cpu"
    assert profiling.resolve_startup_profile(None, " ALL ") == "all"
    assert profiling.resolve_startup_profile(None, None) is None


def test_resolve_startup_profile_env_off_and_invalid():
    for off in ("", "off", "0", "false", "OFF"):
        assert profiling.resolve_startup_profile(None, off) is None
    # invalid env never blocks startup — warned and ignored
    assert profiling.resolve_startup_profile(None, "tracemalloc") is None


def test_apply_startup_profile_arms_cpu_and_mem():
    from ubongo.repl import _apply_startup_profile

    state = _state()
    assert _apply_startup_profile(None, state) is None
    assert state.cpu_profile is False

    notice = _apply_startup_profile("all", state)
    assert state.cpu_profile is True
    assert profiling.mem_active() is True
    assert "Profiling armed at startup" in notice
    assert "cpu" in notice and "mem" in notice


def _stub_response(text="ok"):
    from ubongo.master import Response

    return Response(
        text=text, ok=True, persona="architect",
        skill_name=None, delivery_token=None,
    )


def test_oneshot_profile_mem_reports_and_stops(monkeypatch, capsys):
    from ubongo import oneshot

    monkeypatch.setattr(oneshot.master, "handle",
                        lambda *a, **k: _stub_response("hi"))
    monkeypatch.setattr(oneshot.queue, "enqueue_for_delivery",
                        lambda *a, **k: None)
    monkeypatch.setattr(oneshot.queue, "flush_delivered", lambda token: None)
    assert oneshot.run("hello", "architect", profile="mem") == 0
    out = capsys.readouterr().out
    assert "Memory growth since baseline" in out
    assert profiling.mem_active() is False  # stopped after the report


def test_oneshot_profile_true_still_means_cpu(monkeypatch, capsys):
    from ubongo import oneshot

    monkeypatch.setattr(oneshot.master, "handle",
                        lambda *a, **k: _stub_response("hi"))
    monkeypatch.setattr(oneshot.queue, "enqueue_for_delivery",
                        lambda *a, **k: None)
    monkeypatch.setattr(oneshot.queue, "flush_delivered", lambda token: None)
    assert oneshot.run("hello", "architect", profile=True) == 0
    out = capsys.readouterr().out
    assert "CPU profile written to" in out
    assert list(profiling.profiles_dir().glob("turn-*.prof"))


def test_web_run_turn_profiles_cpu_when_knob_set(monkeypatch):
    from ubongo.web import turn

    monkeypatch.setattr(turn, "_startup_profile", "cpu")
    monkeypatch.setattr(turn.master, "handle",
                        lambda *a, **k: _stub_response("hi"))
    monkeypatch.setattr(turn.queue, "flush_delivered", lambda token: None)
    response = turn.run_turn("hello", "architect", auto_mode=False)
    assert response.text == "hi"
    assert list(profiling.profiles_dir().glob("turn-*.prof"))
