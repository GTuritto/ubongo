from __future__ import annotations

from pathlib import Path

import pytest

from ubongo import commands, repl
from ubongo.commands import Command, ReplState
from ubongo.delivery import queue
from ubongo.memory import store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")
    store.bootstrap()
    yield
    store.set_db_path(None)


def _state() -> ReplState:
    return ReplState(persona="architect", auto_mode=False, pending_skill=None, pending_workflow=None)


# ---------- registry mechanism (commands.py) ----------

def test_split_command():
    assert commands.split_command("/trace 3") == ("trace", "3")
    assert commands.split_command("/AGENTS") == ("agents", "")
    assert commands.split_command("/") == ("", "")
    assert commands.split_command("/mode  brief  collaborative") == ("mode", "brief  collaborative")


def test_dispatch_runs_registered_handler():
    seen = {}
    reg = {"ping": Command(lambda line, st: f"pong:{line}", "/ping")}
    out = commands.dispatch(reg, "ping", "/ping hi", _state())
    assert out == "pong:/ping hi"


def test_dispatch_unknown_returns_sentinel():
    assert commands.dispatch({}, "nope", "/nope", _state()) is commands.UNKNOWN


def test_handler_can_mutate_state():
    def handler(line, st):
        st.pending_skill = "x"
        return None
    st = _state()
    assert commands.dispatch({"s": Command(handler, "/s")}, "s", "/s", st) is None
    assert st.pending_skill == "x"


def test_help_banner_derives_from_registry():
    reg = {"a": Command(lambda l, s: "", "/a <x>"), "b": Command(lambda l, s: "", "/b")}
    banner = commands.help_banner(reg, extra=("/exit",))
    assert banner == "Try /a <x>, /b, /exit."


# ---------- the REPL's registry wiring (repl.py) ----------

def test_repl_registry_covers_the_rich_commands():
    # The 18 rich slash commands all live behind the registry seam now.
    for name in ("trace", "queue", "decisions", "agents", "skills", "policy",
                 "exec", "mode", "optimize", "evaluate", "evolution",
                 "improvements", "recall", "audit", "conflicts", "reload",
                 "skill", "summary"):
        assert name in repl.COMMANDS, name


def test_help_banner_lists_every_registered_command():
    for name in repl.COMMANDS:
        assert f"/{name}" in repl._HELP_COMMANDS
    # plus the persona/exit fallbacks
    for token in ("/architect", "/operator", "/casual", "/auto", "/exit"):
        assert token in repl._HELP_COMMANDS


def test_skill_handler_sets_pending_skill_on_known_skill():
    st = _state()
    # 'summarize-conversation' ships with v0.1.
    out = repl._cmd_skill("/skill summarize-conversation", st)
    assert st.pending_skill == "summarize-conversation"
    assert "summarize-conversation" in out


def test_skill_handler_rejects_unknown_without_mutating_state():
    st = _state()
    out = repl._cmd_skill("/skill phantom", st)
    assert st.pending_skill is None
    assert out == "Unknown skill: phantom."


def test_int_arg_parser_shared_across_commands():
    assert repl._parse_int_arg("/queue", "queue", 10) == 10
    assert repl._parse_int_arg("/trace 5", "trace", 1) == 5
    assert repl._parse_int_arg("/decisions abc", "decisions", 10) is None
    assert repl._parse_int_arg("/queue 0", "queue", 10) is None


# ---------- emit routes command output through the queue (ADR-0002) ----------

def test_emit_queues_command_output_with_command_source(capsys):
    repl.emit("hello from a command")
    captured = capsys.readouterr()
    assert "hello from a command" in captured.out
    rows = queue.last_n(10)
    assert any(r.source == "command" and "hello from a command" in r.content for r in rows)


def test_queue_view_hides_command_rows():
    # A model turn (response) and a command output share the queue...
    queue.enqueue("an assistant reply", source="response")
    repl.emit("a command output")
    # ...but /queue shows only the assistant turn.
    rendered = repl._render_queue_table()
    assert "an assistant reply" in rendered
    assert "a command output" not in rendered
