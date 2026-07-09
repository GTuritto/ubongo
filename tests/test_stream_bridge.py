"""Per-turn event streaming (v0.6 phase 00): the console's bridge. Drives the
forwarder directly with a mocked run_turn — no HTTP, no FastAPI extra. Covers
order, single-flight, exception handling, and handler cleanup."""

from __future__ import annotations

import json
import os
import threading
from types import SimpleNamespace

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import events  # noqa: E402
from ubongo.web.console import stream_bridge as sb  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate():
    events.clear()
    sb._reset_for_tests()
    yield
    sb._reset_for_tests()
    events.clear()


def _frames(stream_id) -> list[dict]:
    """Drain event_stream into parsed dicts (blocks until the terminal frame)."""
    out = []
    for frame in sb.event_stream(stream_id):
        assert frame.startswith("data: ") and frame.endswith("\n\n")
        out.append(json.loads(frame[len("data: "):].strip()))
    return out


def _scripted_run_turn(message, persona, *, auto_mode=True):
    events.dispatch("after_classify", {"classification": {"intent": "technical", "task_type": "technical"}})
    events.dispatch("after_plan", {"workflow": {"agents": ["architect", "evaluator"],
                                                "execution_mode": "sequential", "persona": "architect"}})
    events.dispatch("agent_started", {"agent": "architect"})
    events.dispatch("agent_completed", {"agent": "architect", "ok": True})
    events.dispatch("after_send", {})
    return SimpleNamespace(ok=True), None


def test_events_stream_in_order_with_terminal(_isolate):
    sid = sb.start_turn("hi", "architect", run_turn=_scripted_run_turn)
    assert sid is not None
    frames = _frames(sid)
    names = [f["event"] for f in frames]
    assert names == ["after_classify", "after_plan", "agent_started",
                     "agent_completed", "after_send", "__end__"]
    assert frames[-1] == {"event": "__end__", "ok": True}
    assert frames[1]["agents"] == ["architect", "evaluator"]   # summarized payload
    assert frames[2]["agent"] == "architect"


def test_single_flight_refuses_second_turn(_isolate):
    gate = threading.Event()

    def _blocking(message, persona, *, auto_mode=True):
        gate.wait(2)
        return SimpleNamespace(ok=True), None

    sid1 = sb.start_turn("a", "operator", run_turn=_blocking)
    assert sid1 is not None
    assert sb.start_turn("b", "operator", run_turn=_blocking) is None   # single-flight
    assert sb.active_stream_id() == sid1
    gate.set()
    _frames(sid1)                                                       # let it finish
    assert sb.active_stream_id() is None
    # a new turn is accepted once the first ended
    sid2 = sb.start_turn("c", "operator", run_turn=_scripted_run_turn)
    assert sid2 is not None and sid2 != sid1
    _frames(sid2)


def test_turn_exception_emits_terminal_and_cleans_up(_isolate):
    def _boom(message, persona, *, auto_mode=True):
        events.dispatch("after_classify", {"classification": {"intent": "x"}})
        raise RuntimeError("model down")

    sid = sb.start_turn("x", "operator", run_turn=_boom)
    frames = _frames(sid)
    assert frames[0]["event"] == "after_classify"
    assert frames[-1]["event"] == "__end__" and frames[-1]["ok"] is False
    assert "model down" in frames[-1]["error"]
    # handlers cleaned up: no forwarded-event handler remains, active cleared
    assert sb.active_stream_id() is None
    for name in sb._FORWARDED:
        assert events._handlers.get(name, []) == []


def test_unknown_stream_id_yields_error_frame(_isolate):
    frames = _frames("nope")
    assert frames == [{"event": "__error__", "reason": "no such stream"}]
