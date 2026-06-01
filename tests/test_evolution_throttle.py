from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.evolution.loop import _should_cycle  # noqa: E402
from ubongo.memory import store  # noqa: E402


@pytest.fixture
def db(tmp_path: Path, monkeypatch):
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")
    monkeypatch.delenv("UBONGO_FAKE_NOW", raising=False)


def _cycle(target, gen, calls, ended_at):
    rid = store.start_evolution_run(target=target, generation=gen)
    store.finish_evolution_run(rid, calls_spent=calls, outcome="completed", ended_at=ended_at)


def test_calls_in_last_hour_window(db, monkeypatch) -> None:
    monkeypatch.setenv("UBONGO_FAKE_NOW", "2026-06-01T12:00:00+00:00")
    _cycle("persona:architect", 1, 10, "2026-06-01T11:30:00.000Z")  # within hour
    _cycle("persona:operator", 1, 7, "2026-06-01T11:55:00.000Z")    # within hour
    _cycle("persona:casual", 1, 99, "2026-06-01T10:30:00.000Z")     # >1h ago, excluded
    assert store.calls_in_last_hour() == 17


def test_calls_in_last_hour_empty(db) -> None:
    assert store.calls_in_last_hour() == 0


def test_seconds_since_last_cycle(db, monkeypatch) -> None:
    monkeypatch.setenv("UBONGO_FAKE_NOW", "2026-06-01T12:00:00+00:00")
    assert store.seconds_since_last_cycle() is None
    _cycle("persona:architect", 1, 1, "2026-06-01T11:55:00.000Z")
    assert abs(store.seconds_since_last_cycle() - 300.0) < 1.0


def test_should_cycle_gate() -> None:
    # status must be running
    assert not _should_cycle(status="paused", remaining=10, seconds_since_last=None, cron=None)
    assert not _should_cycle(status="off", remaining=10, seconds_since_last=None, cron=None)
    # budget must have room
    assert not _should_cycle(status="running", remaining=0, seconds_since_last=None, cron=None)
    # happy path
    assert _should_cycle(status="running", remaining=5, seconds_since_last=None, cron=None)


def test_should_cycle_cron_interval() -> None:
    # cron=300s, only 100s elapsed -> wait
    assert not _should_cycle(status="running", remaining=5, seconds_since_last=100.0, cron=300)
    # 400s elapsed -> go
    assert _should_cycle(status="running", remaining=5, seconds_since_last=400.0, cron=300)
    # no prior cycle (None) -> go regardless of cron
    assert _should_cycle(status="running", remaining=5, seconds_since_last=None, cron=300)
