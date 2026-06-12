from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import patch

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import daemon  # noqa: E402


# ---------- the shared gate ----------


def test_should_cycle_gates_status_budget_and_cron():
    ok = dict(status="running", remaining=5, seconds_since_last=None, cron=None)
    assert daemon.should_cycle(**ok)
    assert not daemon.should_cycle(**{**ok, "status": "paused"})
    assert not daemon.should_cycle(**{**ok, "remaining": 0})
    assert not daemon.should_cycle(**{**ok, "seconds_since_last": 5.0, "cron": 60})
    assert daemon.should_cycle(**{**ok, "seconds_since_last": 90.0, "cron": 60})
    # malformed cron never blocks
    assert daemon.should_cycle(**{**ok, "seconds_since_last": 5.0, "cron": "bogus"})


# ---------- lifecycle ----------


class _SyncLoop(daemon.DaemonLoop):
    name = "testd"

    def __init__(self, **kw):
        super().__init__(sleep=kw.pop("sleep", lambda s: time.sleep(s)),
                         tick_seconds=kw.pop("tick_seconds", 0.01))
        self.cycles = 0
        self.boom = False

    def run_cycle(self):
        self.cycles += 1
        if self.boom:
            raise RuntimeError("cycle says no")


class _AsyncLoop(_SyncLoop):
    def __init__(self, **kw):
        kw.setdefault("sleep", asyncio.sleep)
        super().__init__(**kw)


def test_sync_loop_start_cycle_stop():
    d = _SyncLoop()
    assert d.start() is True
    for _ in range(100):
        if d.cycles >= 2:
            break
        time.sleep(0.01)
    d.stop()
    assert d.cycles >= 2
    assert not d._thread.is_alive()


def test_async_loop_start_cycle_stop():
    d = _AsyncLoop()
    assert d.start() is True
    for _ in range(100):
        if d.cycles >= 2:
            break
        time.sleep(0.01)
    d.stop()
    assert d.cycles >= 2
    assert not d._thread.is_alive()


def test_cycle_exception_is_swallowed_and_loop_continues():
    d = _SyncLoop()
    d.boom = True
    assert d.start() is True
    for _ in range(100):
        if d.cycles >= 3:
            break
        time.sleep(0.01)
    d.stop()
    assert d.cycles >= 3  # kept cycling despite every cycle raising


def test_disabled_never_spawns():
    class _Off(_SyncLoop):
        def enabled(self):
            return False

    d = _Off()
    assert d.start() is False
    assert d._thread is None


# ---------- off-switch parity (candidate 15.3) ----------


def test_evolution_gains_disable_env_switch(monkeypatch):
    from ubongo.evolution.loop import EvolutionLoop

    monkeypatch.setenv("UBONGO_DISABLE_EVOLUTION", "1")
    with patch("ubongo.evolution.loop.load_evolution",
               return_value={"enabled": True}):
        assert EvolutionLoop().start() is False
