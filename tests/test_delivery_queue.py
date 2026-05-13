from __future__ import annotations

import os
from pathlib import Path

import pytest

from ubongo.delivery import queue
from ubongo.memory import store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")
    store.bootstrap()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")


def _all_rows():
    conn = store.connection()
    return conn.execute(
        "SELECT id, content, urgency, source, delivered_at FROM notification_queue ORDER BY id"
    ).fetchall()


def test_enqueue_inserts_row_and_returns_id():
    rid = queue.enqueue("hello", source="response")
    rows = _all_rows()
    assert len(rows) == 1
    assert rows[0]["id"] == rid
    assert rows[0]["content"] == "hello"
    assert rows[0]["urgency"] == "urgent"
    assert rows[0]["source"] == "response"
    assert rows[0]["delivered_at"] is None


def test_enqueue_rejects_invalid_urgency():
    with pytest.raises(ValueError):
        queue.enqueue("x", urgency="critical")  # type: ignore[arg-type]


def test_dequeue_round_trip_returns_same_row():
    rid = queue.enqueue("hi there", source="response", metadata={"persona": "casual"})
    row = queue.dequeue_deliverable()
    assert row is not None
    assert row.id == rid
    assert row.content == "hi there"
    assert row.source == "response"
    assert row.metadata == {"persona": "casual"}
    assert row.delivered_at is None


def test_urgency_ordering_urgent_first():
    a = queue.enqueue("low one", urgency="low")
    b = queue.enqueue("urgent one", urgency="urgent")
    c = queue.enqueue("normal one", urgency="normal")
    row = queue.dequeue_deliverable()
    assert row is not None and row.id == b
    queue.mark_delivered(b)
    row = queue.dequeue_deliverable()
    assert row is not None and row.id == c
    queue.mark_delivered(c)
    row = queue.dequeue_deliverable()
    assert row is not None and row.id == a


def test_deliver_after_in_future_is_skipped():
    queue.enqueue(
        "later",
        urgency="urgent",
        deliver_after="2999-01-01T00:00:00.000Z",
    )
    queue.enqueue("now", urgency="urgent")
    row = queue.dequeue_deliverable()
    assert row is not None
    assert row.content == "now"


def test_expired_row_is_skipped():
    queue.enqueue(
        "stale",
        urgency="urgent",
        expires_at="1970-01-01T00:00:00.000Z",
    )
    queue.enqueue("fresh", urgency="urgent")
    row = queue.dequeue_deliverable()
    assert row is not None
    assert row.content == "fresh"


def test_mark_delivered_removes_from_deliverable_set():
    rid = queue.enqueue("once", urgency="urgent")
    queue.mark_delivered(rid)
    assert queue.dequeue_deliverable() is None
    rows = _all_rows()
    assert rows[0]["delivered_at"] is not None


def test_dequeue_empty_returns_none():
    assert queue.dequeue_deliverable() is None


def test_last_n_returns_newest_first():
    queue.enqueue("a")
    queue.enqueue("b")
    queue.enqueue("c")
    rows = queue.last_n(2)
    assert [r.content for r in rows] == ["c", "b"]


def test_last_n_zero_returns_empty():
    queue.enqueue("a")
    assert queue.last_n(0) == []


def test_metadata_round_trip_handles_none():
    rid = queue.enqueue("plain", source="response")
    row = queue.dequeue_deliverable()
    assert row is not None and row.id == rid
    assert row.metadata is None


def test_fake_now_drives_created_at(monkeypatch):
    monkeypatch.setenv("UBONGO_FAKE_NOW", "2030-06-15T12:00:00+00:00")
    queue.enqueue("frozen")
    row = queue.dequeue_deliverable()
    assert row is not None
    assert row.created_at.startswith("2030-06-15T12:00:00")


# --- Code-review regression tests (2026-05-13) ---


def test_enqueue_for_delivery_succeeds_with_stale_undelivered_row():
    """Regression for review finding #3: a stale undelivered row from a prior
    turn must not break delivery of the current row."""
    from ubongo import events

    events.clear()
    stale_id = queue.enqueue("stale leftover", source="response")
    token = queue.enqueue_for_delivery(
        "fresh response",
        source="response",
        after_send_payload={"persona": "casual"},
    )
    assert token.row_id is not None
    assert token.row_id != stale_id
    queue.flush_delivered(token)
    fresh = queue.get_row(token.row_id)
    assert fresh is not None
    assert fresh.delivered_at is not None
    # stale row remains undelivered (caller responsibility)
    stale = queue.get_row(stale_id)
    assert stale is not None and stale.delivered_at is None


def test_flush_delivered_keeps_row_pending_when_after_send_fails():
    """Regression for review finding #4: after_send handler exception must not
    leave the row marked delivered (silent data loss otherwise)."""
    from ubongo import events

    events.clear()

    def boom(_payload):
        raise RuntimeError("vault disk full")

    events.register("after_send", boom)
    token = queue.enqueue_for_delivery(
        "fresh response",
        source="response",
        after_send_payload={"persona": "casual"},
    )
    assert token.row_id is not None
    queue.flush_delivered(token)
    row = queue.get_row(token.row_id)
    assert row is not None
    assert row.delivered_at is None  # still pending; can be retried


def test_flush_delivered_marks_delivered_when_after_send_ok():
    """Companion to the failing case: a successful handler still marks
    the row delivered."""
    from ubongo import events

    events.clear()
    seen: list = []
    events.register("after_send", seen.append)

    token = queue.enqueue_for_delivery(
        "fresh response",
        source="response",
        after_send_payload={"persona": "casual"},
    )
    assert token.row_id is not None
    queue.flush_delivered(token)
    row = queue.get_row(token.row_id)
    assert row is not None
    assert row.delivered_at is not None
    assert seen == [{"persona": "casual"}]
