"""Per-turn event streaming (v0.6 phase 00) — the one new primitive.

A turn normally runs synchronously and returns once. To stream it live, the
console runs `channel.run_turn` on a **background thread** and forwards the named
bus events to the browser as they fire. This module is that bridge, HTTP-free and
unit-testable: `start_turn` registers handlers + spawns the turn; `event_stream`
drains the session's queue as SSE frames until a terminal `__end__`.

**Single-flight** (D1): one active turn at a time — a second `start_turn` while
one is live is refused. The console serves one user at one keyboard, and the
console server starts no daemons, so the only events that fire are the turn's
own. No correlation plumbing needed; a `contextvar` variant is a later phase.

The stream is observation only — it never touches orchestration (ADR-0002/0003).
"""

from __future__ import annotations

import json
import logging
import queue
import threading

from ubongo import events

logger = logging.getLogger("ubongo.web.console.bridge")

# The pipeline events forwarded to the browser, in the order they fire.
_FORWARDED = (
    "after_classify", "after_plan",
    "agent_started", "agent_completed", "agent_failed",
    "after_govern", "after_compose", "after_send",
)
_END = "__end__"          # terminal frame (carries ok); closes the SSE stream
_SENTINEL = object()      # internal queue close marker


class _Session:
    def __init__(self, stream_id: str, message: str, persona: str, auto_mode: bool) -> None:
        self.id = stream_id
        self.message = message
        self.persona = persona
        self.auto_mode = auto_mode
        self.q: "queue.Queue" = queue.Queue()
        self.handlers: list[tuple[str, object]] = []


_active: "_Session | None" = None
_by_id: "dict[str, _Session]" = {}      # retained so a late /stream still drains
_counter = 0
_lock = threading.Lock()


def _next_id() -> str:
    global _counter
    _counter += 1
    return f"s{_counter}"


def _summarize(name: str, payload: dict) -> dict:
    """A small JSON-safe view of an event payload (the full payloads carry
    dataclass-as-dict blobs we don't need in the stream)."""
    p = payload or {}
    if name in ("agent_started", "agent_completed", "agent_failed"):
        return {"agent": p.get("agent"), "ok": p.get("ok"),
                "error": p.get("error"), "retried": p.get("retried")}
    if name == "after_plan":
        wf = p.get("workflow") or {}
        return {"agents": wf.get("agents"), "mode": wf.get("execution_mode"),
                "persona": wf.get("persona")}
    if name == "after_classify":
        cls = p.get("classification") or {}
        return {"intent": cls.get("intent"), "task_type": cls.get("task_type")}
    if name == "after_govern":
        dec = p.get("decision") or {}
        return {"action": dec.get("action")}
    if name == "after_compose":
        return {"persona": p.get("persona")}
    if name == "after_send":
        return {"done": True}
    return {}


def _register(sess: _Session) -> None:
    for name in _FORWARDED:
        def handler(payload, _name=name, _sess=sess):
            # Single-flight guard: only forward to the currently active session.
            if _active is _sess:
                _sess.q.put({"event": _name, **_summarize(_name, payload)})
        events.register(name, handler)
        sess.handlers.append((name, handler))


def _unregister(sess: _Session) -> None:
    for name, handler in sess.handlers:
        events.unregister(name, handler)
    sess.handlers.clear()


def start_turn(message: str, persona: str, *, auto_mode: bool = True, run_turn=None) -> "str | None":
    """Begin a streamed turn. Returns a stream_id, or None if a turn is already
    active (single-flight). `run_turn` is injectable for tests; production uses
    `channel.run_turn`."""
    global _active
    with _lock:
        if _active is not None:
            return None
        sess = _Session(_next_id(), message, persona, auto_mode)
        _active = sess
        _by_id[sess.id] = sess
    _register(sess)

    if run_turn is None:
        from ubongo import channel
        run_turn = channel.run_turn

    def _drive() -> None:
        global _active
        ok = False
        try:
            response, _ = run_turn(sess.message, sess.persona, auto_mode=sess.auto_mode)
            ok = bool(getattr(response, "ok", False))
            sess.q.put({"event": _END, "ok": ok})
        except Exception as exc:  # never leave the stream hanging
            logger.warning("console_turn_failed", extra={"cause": str(exc)})
            sess.q.put({"event": _END, "ok": False, "error": str(exc)[:200]})
        finally:
            _unregister(sess)
            sess.q.put(_SENTINEL)
            with _lock:
                if _active is sess:
                    _active = None

    threading.Thread(target=_drive, name=f"console-turn-{sess.id}", daemon=True).start()
    return sess.id


def event_stream(stream_id: str):
    """Yield SSE frames for `stream_id` until the terminal `__end__`. A generator
    (sync) the FastAPI layer adapts to its async response."""
    sess = _by_id.get(stream_id)
    if sess is None:
        yield _sse({"event": "__error__", "reason": "no such stream"})
        return
    try:
        while True:
            item = sess.q.get()
            if item is _SENTINEL:
                return
            yield _sse(item)
    finally:
        _by_id.pop(stream_id, None)  # drained; let it be collected


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def active_stream_id() -> "str | None":
    return _active.id if _active is not None else None


def _reset_for_tests() -> None:
    """Drop any active session + its handlers (test isolation only)."""
    global _active
    if _active is not None:
        _unregister(_active)
        _active = None
    _by_id.clear()
