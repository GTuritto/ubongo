from __future__ import annotations

from pathlib import Path

import pytest

from ubongo import repl
from ubongo.delivery import queue
from ubongo.memory import store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")
    store.bootstrap()
    yield
    store.set_db_path(None)


def test_parse_queue_no_arg_defaults_to_10():
    assert repl._parse_queue_command("/queue") == 10


def test_parse_queue_explicit_arg():
    assert repl._parse_queue_command("/queue 25") == 25


def test_parse_queue_rejects_non_int():
    assert repl._parse_queue_command("/queue abc") is None


def test_parse_queue_rejects_zero_or_negative():
    assert repl._parse_queue_command("/queue 0") is None
    assert repl._parse_queue_command("/queue -3") is None


def test_render_empty_queue():
    assert repl._render_queue_table() == "Queue is empty."


def test_render_queue_shows_rows_newest_first():
    rid_a = queue.enqueue("first message", source="response")
    queue.mark_delivered(rid_a)
    rid_b = queue.enqueue("second message", source="response")
    queue.mark_delivered(rid_b)
    rid_c = queue.enqueue("third message", source="response")

    table = repl._render_queue_table()
    lines = table.splitlines()
    assert lines[0] == "Recent queue (last 10):"
    # Newest first: c, b, a
    assert "third message" in lines[1]
    assert "second message" in lines[2]
    assert "first message" in lines[3]
    # Delivered/undelivered marker
    assert "—" in lines[1]  # rid_c is not yet delivered
    assert "—" not in lines[2].split("response")[0]  # rid_b is delivered


def test_render_queue_truncates_long_preview():
    long_text = "x" * 200
    queue.enqueue(long_text, source="response")
    table = repl._render_queue_table()
    # last 60 chars + ellipsis
    assert "…" in table
    assert "x" * 60 in table
    assert "x" * 70 not in table


def test_render_queue_respects_n_argument():
    for i in range(5):
        queue.enqueue(f"msg {i}", source="response")
    table = repl._render_queue_table(n=2)
    lines = table.splitlines()
    assert lines[0] == "Recent queue (last 2):"
    assert len(lines) == 3  # header + 2 rows
    assert "msg 4" in lines[1]
    assert "msg 3" in lines[2]


def test_render_queue_shows_dash_for_null_source():
    queue.enqueue("no source")
    table = repl._render_queue_table()
    # source column shows "—" when source is None
    lines = table.splitlines()
    assert "—" in lines[1]


def test_help_line_mentions_queue():
    assert "/queue" in repl._HELP_COMMANDS
