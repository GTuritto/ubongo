from __future__ import annotations

import pytest

from ubongo.repl import DEFAULT_PERSONA, echo, handle_slash


def test_echo_format() -> None:
    assert echo("architect", "hello") == "[architect] hello"


@pytest.mark.parametrize("name", ["architect", "operator", "casual"])
def test_slash_switches_to_named_persona(name: str) -> None:
    new_persona, keep_going, msg = handle_slash(f"/{name}", "architect")
    assert new_persona == name
    assert keep_going is True
    assert msg == f"Switched to {name}."


def test_slash_auto_resets_to_default() -> None:
    new_persona, keep_going, msg = handle_slash("/auto", "casual")
    assert new_persona == DEFAULT_PERSONA
    assert keep_going is True
    assert "Phase 3" in msg


def test_slash_exit_stops_loop() -> None:
    new_persona, keep_going, msg = handle_slash("/exit", "operator")
    assert new_persona == "operator"
    assert keep_going is False
    assert msg == "Goodbye."


def test_slash_unknown_command_reports_and_continues() -> None:
    new_persona, keep_going, msg = handle_slash("/foo", "casual")
    assert new_persona == "casual"
    assert keep_going is True
    assert msg.startswith("Unknown command: /foo.")


def test_slash_is_case_insensitive_and_whitespace_tolerant() -> None:
    new_persona, _, msg = handle_slash("/  Operator  ", "architect")
    assert new_persona == "operator"
    assert msg == "Switched to operator."


def test_slash_bare_slash_is_an_empty_command() -> None:
    new_persona, keep_going, msg = handle_slash("/", "architect")
    assert new_persona == "architect"
    assert keep_going is True
    assert "Empty command" in msg
