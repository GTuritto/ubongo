from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.evolution import loop  # noqa: E402
from ubongo.evolution.loop import EvolutionLoop  # noqa: E402
from ubongo.memory import evolution_state
from ubongo.memory import store  # noqa: E402


@pytest.fixture
def db(tmp_path: Path):
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")


def test_status_defaults_paused(db) -> None:
    assert evolution_state.get_evolution_status() == "paused"


def test_set_get_status(db) -> None:
    evolution_state.set_evolution_status("running")
    assert evolution_state.get_evolution_status() == "running"
    evolution_state.set_evolution_status("off")
    assert evolution_state.get_evolution_status() == "off"


def test_invalid_status_rejected(db) -> None:
    with pytest.raises(ValueError):
        evolution_state.set_evolution_status("bogus")


def test_maybe_run_cycle_skips_when_paused(db, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(loop, "run_one_cycle", lambda **kw: calls.append(kw))
    evolution_state.set_evolution_status("paused")
    EvolutionLoop()._maybe_run_cycle()
    assert calls == []


def test_maybe_run_cycle_runs_when_running(db, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(loop, "run_one_cycle", lambda **kw: calls.append(kw))
    evolution_state.set_evolution_status("running")
    EvolutionLoop()._maybe_run_cycle()
    assert len(calls) == 1
    assert calls[0]["budget"].limit == 30  # seeded from max_calls_per_hour - 0


def test_maybe_run_cycle_skips_when_off(db, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(loop, "run_one_cycle", lambda **kw: calls.append(kw))
    evolution_state.set_evolution_status("off")
    EvolutionLoop()._maybe_run_cycle()
    assert calls == []


def test_start_returns_false_when_disabled(db, monkeypatch) -> None:
    monkeypatch.setattr(loop, "load_evolution", lambda: {"enabled": False})
    el = EvolutionLoop()
    assert el.start() is False
    assert el._thread is None


def test_start_stop_when_enabled_paused(db, monkeypatch) -> None:
    monkeypatch.setattr(loop, "load_evolution", lambda: {"enabled": True, "max_calls_per_hour": 30})
    # Default status is paused, so the running thread never spends.
    el = EvolutionLoop(tick_seconds=0.01)
    started = el.start()
    assert started is True
    assert el._thread is not None and el._thread.is_alive()
    el.stop(timeout=2.0)
    assert not el._thread.is_alive()
