"""Standing jobs (v0.5 phase 06): the proactive-output loop. Covers the policy
(quiet hours + TTL), the runner (delivered / held / parked / error + the
default-deny sweep), due-job selection, the daemon gate, the drain, and the
/jobs command surface. master.handle is mocked — no real turns run."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.delivery import queue  # noqa: E402
from ubongo.jobs import commands as jobs_commands  # noqa: E402
from ubongo.jobs import delivery, loop, policy, runner  # noqa: E402
from ubongo.memory import jobs_state, store, trace  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")
    store.bootstrap()
    yield
    store.set_db_path(None)


def _fake_now(monkeypatch, iso):
    monkeypatch.setenv("UBONGO_FAKE_NOW", iso)


def _response(text="digest", approval=None):
    return SimpleNamespace(
        text=text, ok=True, approval=approval,
        delivery_token=queue.DeliveryToken(row_id=None, after_send_payload=None),
    )


def _job(**o):
    base = {"name": "news", "enabled": True, "schedule_seconds": 3600,
            "persona": "operator", "prompt": "do it"}
    base.update(o)
    return base


def _patch_cfg(monkeypatch, *, jobs=None, defs=None):
    if jobs is not None:
        monkeypatch.setattr(runner, "load_jobs", lambda: jobs)
    if defs is not None:
        monkeypatch.setattr(runner, "load_job_definitions", lambda: defs)


def _real_decision() -> int:
    wfid = trace.append_workflow_run(
        conversation_id=1, message_id=1, classification={}, workflow={},
        execution_mode="sequential", outcome="success", started_at=store.now_iso())
    return trace.append_governance_decision(
        wfid, intent="command", risk="destructive", confidence=0.9,
        reversibility="irreversible", action="require_approval")


# --- policy ----------------------------------------------------------------


def test_quiet_hours_wraparound_and_none():
    night = datetime(2026, 6, 20, 3, tzinfo=timezone.utc)
    day = datetime(2026, 6, 20, 12, tzinfo=timezone.utc)
    assert policy.in_quiet_hours(night, [23, 7]) is True
    assert policy.in_quiet_hours(day, [23, 7]) is False
    assert policy.in_quiet_hours(night, None) is False     # unset = never quiet
    assert policy.in_quiet_hours(night, [5, 5]) is False    # degenerate = never


def test_deliver_after_boundary():
    night = datetime(2026, 6, 20, 3, tzinfo=timezone.utc)
    assert policy.deliver_after([23, 7], now=night) == "2026-06-20T07:00:00.000Z"
    day = datetime(2026, 6, 20, 12, tzinfo=timezone.utc)
    assert policy.deliver_after([23, 7], now=day) is None   # deliverable now


def test_raise_expires_at(monkeypatch):
    _fake_now(monkeypatch, "2026-06-20T12:00:00.000Z")
    assert policy.raise_expires_at(24) == "2026-06-21T12:00:00.000Z"


# --- runner: the three delivery verdicts -----------------------------------


def test_run_job_delivered_enqueues_proactive(monkeypatch):
    _patch_cfg(monkeypatch, jobs={"quiet_hours": None, "raise_ttl_hours": 24})
    monkeypatch.setattr(runner.master, "handle", lambda *a, **k: _response("the digest"))
    jobs_state.ensure_job("news")
    result = runner.run_job(_job())
    assert result.outcome == "delivered"
    rows = delivery.deliverable_proactive()
    assert len(rows) == 1 and rows[0].content == "the digest"
    assert rows[0].source == delivery.SOURCE_PROACTIVE
    assert jobs_state.get_job("news")["next_run"] is not None  # rescheduled


def test_run_job_held_during_quiet_hours(monkeypatch):
    _fake_now(monkeypatch, "2026-06-20T03:00:00.000Z")
    _patch_cfg(monkeypatch, jobs={"quiet_hours": [23, 7], "raise_ttl_hours": 24})
    monkeypatch.setattr(runner.master, "handle", lambda *a, **k: _response("night digest"))
    jobs_state.ensure_job("news")
    result = runner.run_job(_job())
    assert result.outcome == "held"
    # Held = enqueued with a future deliver_after, so NOT yet deliverable.
    assert delivery.deliverable_proactive() == []
    assert jobs_state.job_runs_recent(1)[0]["outcome"] == "held"


def test_run_job_parks_on_approval_and_raises(monkeypatch):
    did = _real_decision()
    _patch_cfg(monkeypatch, jobs={"quiet_hours": None, "raise_ttl_hours": 24})
    approval = SimpleNamespace(decision_id=did, summary="needs the news tool")
    monkeypatch.setattr(runner.master, "handle",
                        lambda *a, **k: _response("ignored", approval=approval))
    jobs_state.ensure_job("news")
    result = runner.run_job(_job())
    assert result.outcome == "parked" and result.decision_id == did
    rows = delivery.deliverable_proactive()
    assert len(rows) == 1 and rows[0].source == delivery.SOURCE_RAISE
    assert f"/approve {did}" in rows[0].content
    assert rows[0].expires_at is not None  # TTL set on the raise
    assert jobs_state.job_runs_recent(1)[0]["outcome"] == "parked"


def test_run_job_error_reschedules_and_records(monkeypatch):
    _patch_cfg(monkeypatch, jobs={"quiet_hours": None, "raise_ttl_hours": 24})
    def _boom(*a, **k):
        raise RuntimeError("model down")
    monkeypatch.setattr(runner.master, "handle", _boom)
    jobs_state.ensure_job("news")
    result = runner.run_job(_job())
    assert result.outcome == "error"
    assert jobs_state.job_runs_recent(1)[0]["outcome"] == "error"
    assert jobs_state.get_job("news")["next_run"] is not None  # still rescheduled


# --- the default-deny TTL sweep --------------------------------------------


def test_sweep_autodeclines_expired_raises(monkeypatch):
    _fake_now(monkeypatch, "2026-06-20T08:00:00.000Z")
    did = _real_decision()
    trace.append_pending_approval(did, message="m", persona="operator",
                                  auto_mode=True, summary="s", why="w")
    jobs_state.finish_job_run(jobs_state.start_job_run("news"),
                              outcome="parked", decision_id=did)
    _patch_cfg(monkeypatch, jobs={"raise_ttl_hours": 1})
    calls = []
    monkeypatch.setattr(runner.master, "resume_approval",
                        lambda d, c: calls.append((d, c)))
    _fake_now(monkeypatch, "2026-06-20T12:00:00.000Z")  # 4h later, TTL=1h elapsed
    results = runner._sweep_expired_raises()
    assert calls == [(did, "n")]  # default-deny
    assert [r.outcome for r in results] == ["skipped"]
    assert jobs_state.job_runs_recent(1)[0]["outcome"] == "skipped"


# --- due-job selection -----------------------------------------------------


def test_due_job_picks_most_overdue_and_skips_future(monkeypatch):
    _fake_now(monkeypatch, "2026-06-20T12:00:00.000Z")
    _patch_cfg(monkeypatch, defs=[_job(name="a"), _job(name="b"), _job(name="c", enabled=False)])
    jobs_state.ensure_job("a"); jobs_state.ensure_job("b")
    jobs_state.mark_run("a", last_run="x", next_run="2026-06-20T09:00:00.000Z", last_outcome="delivered")
    jobs_state.mark_run("b", last_run="x", next_run="2026-06-20T18:00:00.000Z", last_outcome="delivered")
    # a is overdue (09:00 < 12:00), b is future (18:00 > 12:00), c is disabled.
    assert runner.due_job()["name"] == "a"
    # Reschedule a into the future and bring b due: now only b is overdue.
    jobs_state.mark_run("a", last_run="x", next_run="2026-06-20T20:00:00.000Z", last_outcome="delivered")
    jobs_state.mark_run("b", last_run="x", next_run="2026-06-20T11:00:00.000Z", last_outcome="delivered")
    assert runner.due_job()["name"] == "b"


def test_run_one_cycle_idle_when_nothing_due(monkeypatch):
    _patch_cfg(monkeypatch, defs=[])
    assert runner.run_one_cycle().outcome == "idle"


# --- the daemon gate -------------------------------------------------------


def test_loop_does_not_cycle_when_paused(monkeypatch):
    monkeypatch.setattr(loop, "load_jobs", lambda: {"max_runs_per_hour": 20, "cron": None})
    jobs_state.set_jobs_status("paused")
    ran = []
    monkeypatch.setattr(runner, "run_one_cycle", lambda: ran.append(1))
    StandingJobsLoop = loop.StandingJobsLoop
    StandingJobsLoop().run_cycle()
    assert ran == []


def test_loop_cycles_when_running(monkeypatch):
    monkeypatch.setattr(loop, "load_jobs", lambda: {"max_runs_per_hour": 20, "cron": None})
    jobs_state.set_jobs_status("running")
    ran = []
    monkeypatch.setattr(runner, "run_one_cycle", lambda: ran.append(1))
    loop.StandingJobsLoop().run_cycle()
    assert ran == [1]


# --- the proactive drain ---------------------------------------------------


def test_drain_proactive_sends_and_marks_delivered():
    queue.enqueue("hello", source=delivery.SOURCE_PROACTIVE)
    queue.enqueue("raise me", source=delivery.SOURCE_RAISE)
    sent = []
    n = delivery.drain_proactive(lambda row: sent.append(row.content))
    assert n == 2 and sorted(sent) == ["hello", "raise me"]
    assert delivery.deliverable_proactive() == []  # all marked delivered


def test_drain_skips_held_and_expired(monkeypatch):
    _fake_now(monkeypatch, "2026-06-20T12:00:00.000Z")
    queue.enqueue("future", source=delivery.SOURCE_PROACTIVE,
                  deliver_after="2026-06-20T23:00:00.000Z")  # quiet-hours hold
    queue.enqueue("dead", source=delivery.SOURCE_RAISE,
                  expires_at="2026-06-20T11:00:00.000Z")     # expired TTL
    n = delivery.drain_proactive(lambda row: None)
    assert n == 0


def test_drain_failed_send_leaves_row_pending():
    queue.enqueue("keep me", source=delivery.SOURCE_PROACTIVE)
    def _boom(row):
        raise RuntimeError("channel down")
    with pytest.raises(RuntimeError):
        delivery.drain_proactive(_boom)
    assert len(delivery.deliverable_proactive()) == 1  # not lost


# --- /jobs command surface -------------------------------------------------


def test_jobs_status_render(monkeypatch):
    monkeypatch.setattr(jobs_commands, "_render_runs", _render := (lambda: "runs"))  # noqa: F841
    out = jobs_commands.render("jobs status")
    assert out.startswith("Standing jobs:")


def test_jobs_control_resume_pause_off():
    assert "resumed" in jobs_commands.render("jobs resume").lower()
    assert jobs_state.get_jobs_status() == "running"
    assert "paused" in jobs_commands.render("jobs pause").lower()
    assert jobs_state.get_jobs_status() == "paused"
    assert "off" in jobs_commands.render("jobs off").lower()
    assert jobs_state.get_jobs_status() == "off"


def test_jobs_run_unknown():
    assert "Unknown job" in jobs_commands.render("jobs run nope")


def test_jobs_unknown_subcommand():
    assert "Unknown subcommand" in jobs_commands.render("jobs frobnicate")
