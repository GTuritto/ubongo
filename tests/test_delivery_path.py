from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context, events, oneshot, repl, skills  # noqa: E402
from ubongo.agents import memory as _agents_memory  # noqa: E402
from ubongo.delivery import queue  # noqa: E402
from ubongo.llm import CompletionResult, LLMError  # noqa: E402
from ubongo.memory import store, vault  # noqa: E402


def _completion(text: str) -> CompletionResult:
    return CompletionResult(text=text, model="test-model", tokens_in=10, tokens_out=10, latency_ms=5, attempts=1)


@pytest.fixture(autouse=True)
def _clean_env(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")
    vault.set_vault_root(tmp_path / "vault")
    skills.set_skills_dir(None)
    skills.reload()
    context.reload()
    events.clear()
    # re-register the production handlers nuked by events.clear()
    events.register("after_send", _agents_memory.default_memory_agent.project_vault)
    yield
    events.clear()
    events.register("after_send", _agents_memory.default_memory_agent.project_vault)
    skills.set_skills_dir(None)
    skills.reload()
    context.reload()
    store.set_db_path(None)
    vault.set_vault_root(None)


def _queue_rows():
    conn = store.connection()
    return conn.execute(
        "SELECT id, content, urgency, source, delivered_at FROM notification_queue ORDER BY id"
    ).fetchall()


def test_happy_path_response_flows_through_queue(capsys) -> None:
    with patch("ubongo.master.complete", return_value=_completion("hello back")):
        rc = oneshot.run("hi", "casual")

    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == "hello back"

    rows = _queue_rows()
    assert len(rows) == 1
    assert rows[0]["content"] == "hello back"
    assert rows[0]["urgency"] == "urgent"
    assert rows[0]["source"] == "response"
    assert rows[0]["delivered_at"] is not None


def test_before_send_fires_before_after_send() -> None:
    sequence: list[str] = []
    events.register("before_send", lambda _p: sequence.append("before"))
    events.register("after_send", lambda _p: sequence.append("after"))

    with patch("ubongo.master.complete", return_value=_completion("ok")):
        oneshot.run("hi", "casual")

    assert sequence == ["before", "after"]


def test_before_send_payload_includes_row_and_metadata() -> None:
    captured: list[dict] = []
    events.register("before_send", captured.append)

    with patch("ubongo.master.complete", return_value=_completion("ok")):
        oneshot.run("hi", "casual")

    assert len(captured) == 1
    payload = captured[0]
    assert payload["content"] == "ok"
    assert payload["urgency"] == "urgent"
    assert payload["source"] == "response"
    assert isinstance(payload["row_id"], int)
    assert payload["metadata"]["persona"] == "casual"


def test_error_path_enqueues_with_source_error_and_skips_after_send(capsys) -> None:
    after_sends: list[dict] = []
    before_sends: list[dict] = []
    events.register("before_send", before_sends.append)
    events.register("after_send", after_sends.append)

    with patch("ubongo.master.complete", side_effect=LLMError("simulated", cause=RuntimeError("nope"))):
        rc = oneshot.run("hi", "casual")

    assert rc == 1
    out = capsys.readouterr().out.strip()
    assert out == "Sorry, I couldn't reach the model. Check the logs."

    rows = _queue_rows()
    assert len(rows) == 1
    assert rows[0]["source"] == "error"
    assert rows[0]["delivered_at"] is not None

    assert len(before_sends) == 1
    assert before_sends[0]["source"] == "error"
    assert after_sends == []  # vault should NOT log a failed turn


def test_vault_still_writes_on_happy_path() -> None:
    import datetime

    with patch("ubongo.master.complete", return_value=_completion("hello back")):
        oneshot.run("hi", "casual")

    note = vault.daily_note_path(datetime.date.today())
    assert note.exists()
    body = note.read_text(encoding="utf-8")
    assert "hello back" in body


def test_vault_does_not_write_on_error_path() -> None:
    import datetime

    with patch("ubongo.master.complete", side_effect=LLMError("simulated", cause=RuntimeError("nope"))):
        oneshot.run("hi", "casual")

    note = vault.daily_note_path(datetime.date.today())
    assert not note.exists()


def test_enqueue_failure_still_prints_and_skips_events(capsys) -> None:
    before_sends: list[dict] = []
    after_sends: list[dict] = []
    events.register("before_send", before_sends.append)
    events.register("after_send", after_sends.append)

    with (
        patch("ubongo.master.complete", return_value=_completion("hello back")),
        patch("ubongo.delivery.queue.enqueue", side_effect=RuntimeError("db down")),
    ):
        rc = oneshot.run("hi", "casual")

    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == "hello back"  # output preserved
    assert before_sends == []
    assert after_sends == []
    # nothing made it into the queue
    assert _queue_rows() == []


def test_enqueue_for_delivery_dispatches_before_send_with_dequeued_row() -> None:
    store.bootstrap()
    captured: list[dict] = []
    events.register("before_send", captured.append)

    token = queue.enqueue_for_delivery(
        "payload",
        source="response",
        after_send_payload={"flag": True},
        metadata={"persona": "casual"},
    )

    assert token.row_id is not None
    assert token.after_send_payload == {"flag": True}
    assert len(captured) == 1
    assert captured[0]["content"] == "payload"
    assert captured[0]["metadata"] == {"persona": "casual"}


def test_flush_delivered_marks_row_and_fires_after_send() -> None:
    store.bootstrap()
    after_sends: list[dict] = []
    events.register("after_send", after_sends.append)

    token = queue.enqueue_for_delivery(
        "payload",
        source="response",
        after_send_payload={"flag": True},
    )
    queue.flush_delivered(token)

    assert after_sends == [{"flag": True}]
    rows = _queue_rows()
    assert rows[0]["delivered_at"] is not None


def test_flush_delivered_skips_after_send_when_payload_none() -> None:
    store.bootstrap()
    after_sends: list[dict] = []
    events.register("after_send", after_sends.append)

    token = queue.enqueue_for_delivery(
        "error msg",
        source="error",
        after_send_payload=None,
    )
    queue.flush_delivered(token)

    assert after_sends == []
    rows = _queue_rows()
    assert rows[0]["delivered_at"] is not None
