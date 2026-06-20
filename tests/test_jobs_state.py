"""Standing-jobs state (v0.5 phase 06): runtime rows, run audit, throttle,
control status, and the expired-parked-raise query (the default-deny TTL)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.memory import jobs_state, store, trace  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")
    store.bootstrap()
    yield
    store.set_db_path(None)


def _fake_now(monkeypatch, iso: str | None):
    if iso is None:
        monkeypatch.delenv("UBONGO_FAKE_NOW", raising=False)
    else:
        monkeypatch.setenv("UBONGO_FAKE_NOW", iso)


# --- runtime rows ----------------------------------------------------------


def test_ensure_job_is_idempotent_and_no_clobber():
    jobs_state.ensure_job("news")
    jobs_state.mark_run("news", last_run="2026-06-20T10:00:00.000Z",
                        next_run="2026-06-21T10:00:00.000Z", last_outcome="delivered")
    jobs_state.ensure_job("news")  # second call must not reset last_run/next_run
    row = jobs_state.get_job("news")
    assert row["last_run"] == "2026-06-20T10:00:00.000Z"
    assert row["last_outcome"] == "delivered"


def test_get_job_absent_is_none_and_all_jobs_sorted():
    assert jobs_state.get_job("missing") is None
    jobs_state.ensure_job("b")
    jobs_state.ensure_job("a")
    assert [j["name"] for j in jobs_state.all_jobs()] == ["a", "b"]


# --- run audit + throttle --------------------------------------------------


def test_start_finish_job_run_and_recent():
    rid = jobs_state.start_job_run("news")
    jobs_state.finish_job_run(rid, outcome="delivered", detail="ok")
    runs = jobs_state.job_runs_recent(5)
    assert runs[0]["outcome"] == "delivered"
    assert runs[0]["job_name"] == "news"


def test_record_job_run_one_shot():
    rid = jobs_state.record_job_run("news", outcome="skipped", detail="expired")
    assert jobs_state.job_runs_recent(1)[0]["id"] == rid


def test_runs_in_last_hour_windowed(monkeypatch):
    # An old finished run falls outside the trailing hour; a fresh one counts.
    _fake_now(monkeypatch, "2026-06-20T08:00:00.000Z")
    jobs_state.finish_job_run(jobs_state.start_job_run("news"), outcome="delivered")
    _fake_now(monkeypatch, "2026-06-20T12:00:00.000Z")
    jobs_state.finish_job_run(jobs_state.start_job_run("news"), outcome="delivered")
    assert jobs_state.runs_in_last_hour() == 1  # only the 12:00 run is within the hour


def test_seconds_since_last_cycle(monkeypatch):
    assert jobs_state.seconds_since_last_cycle() is None
    _fake_now(monkeypatch, "2026-06-20T12:00:00.000Z")
    jobs_state.finish_job_run(jobs_state.start_job_run("news"), outcome="delivered")
    _fake_now(monkeypatch, "2026-06-20T12:01:00.000Z")
    assert jobs_state.seconds_since_last_cycle() == pytest.approx(60.0, abs=1.0)


# --- control status --------------------------------------------------------


def test_status_defaults_paused_and_persists():
    assert jobs_state.get_jobs_status() == "paused"
    jobs_state.set_jobs_status("running")
    assert jobs_state.get_jobs_status() == "running"


def test_status_rejects_garbage():
    with pytest.raises(ValueError):
        jobs_state.set_jobs_status("bogus")


# --- the default-deny TTL query --------------------------------------------


def _parked_decision(message="do thing") -> int:
    """Build the FK chain workflow_run -> governance_decision -> pending_approval
    and return the decision_id (the parked raise's target)."""
    wfid = trace.append_workflow_run(
        conversation_id=1, message_id=1, classification={}, workflow={},
        execution_mode="sequential", outcome="success", started_at=store.now_iso(),
    )
    did = trace.append_governance_decision(
        wfid, intent="command", risk="destructive", confidence=0.9,
        reversibility="irreversible", action="require_approval",
    )
    trace.append_pending_approval(did, message=message, persona="operator",
                                  auto_mode=True, summary="s", why="w")
    return did


def test_expired_parked_decisions_returns_old_pending(monkeypatch):
    _fake_now(monkeypatch, "2026-06-20T08:00:00.000Z")
    did = _parked_decision()
    jobs_state.finish_job_run(jobs_state.start_job_run("news"),
                              outcome="parked", decision_id=did)
    # 4 hours later, a 1-hour TTL has elapsed -> the raise is expired.
    _fake_now(monkeypatch, "2026-06-20T12:00:00.000Z")
    expired = jobs_state.expired_parked_decisions(3600.0)
    assert [e["decision_id"] for e in expired] == [did]


def test_expired_parked_decisions_skips_recent_and_resolved(monkeypatch):
    _fake_now(monkeypatch, "2026-06-20T12:00:00.000Z")
    did_recent = _parked_decision("recent")
    jobs_state.finish_job_run(jobs_state.start_job_run("news"),
                              outcome="parked", decision_id=did_recent)
    # Still within the TTL window -> not expired.
    assert jobs_state.expired_parked_decisions(3600.0) == []
    # An old but already-resolved approval is excluded (status != pending),
    # even though it is well past the TTL.
    _fake_now(monkeypatch, "2026-06-20T08:00:00.000Z")
    did_resolved = _parked_decision("resolved")
    jobs_state.finish_job_run(jobs_state.start_job_run("news"),
                              outcome="parked", decision_id=did_resolved)
    trace.resolve_pending_approval(did_resolved, "declined")
    _fake_now(monkeypatch, "2026-06-20T20:00:00.000Z")
    expired_ids = [e["decision_id"] for e in jobs_state.expired_parked_decisions(3600.0)]
    assert did_resolved not in expired_ids
