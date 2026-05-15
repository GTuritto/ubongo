from __future__ import annotations

from unittest.mock import patch

import pytest

from ubongo.repl import DEFAULT_PERSONA, _prompt_repair_retry, handle_slash


@pytest.mark.parametrize("name", ["architect", "operator", "casual"])
def test_slash_switches_to_named_persona(name: str) -> None:
    new_persona, keep_going, msg, auto_change = handle_slash(f"/{name}", "architect")
    assert new_persona == name
    assert keep_going is True
    assert msg == f"Switched to {name}."
    assert auto_change is False  # named persona disables auto


def test_slash_auto_enables_auto_mode_keeps_current_persona() -> None:
    new_persona, keep_going, msg, auto_change = handle_slash("/auto", "casual")
    assert new_persona == "casual"  # unchanged; classifier picks on next turn
    assert keep_going is True
    assert msg == "Auto routing enabled."
    assert auto_change is True


def test_slash_exit_stops_loop() -> None:
    new_persona, keep_going, msg, auto_change = handle_slash("/exit", "operator")
    assert new_persona == "operator"
    assert keep_going is False
    assert msg == "Goodbye."
    assert auto_change is None  # /exit doesn't touch auto_mode


def test_slash_unknown_command_reports_and_continues() -> None:
    new_persona, keep_going, msg, auto_change = handle_slash("/foo", "casual")
    assert new_persona == "casual"
    assert keep_going is True
    assert msg.startswith("Unknown command: /foo.")
    assert auto_change is None


def test_slash_is_case_insensitive_and_whitespace_tolerant() -> None:
    new_persona, _, msg, auto_change = handle_slash("/  Operator  ", "architect")
    assert new_persona == "operator"
    assert msg == "Switched to operator."
    assert auto_change is False


def test_slash_bare_slash_is_an_empty_command() -> None:
    new_persona, keep_going, msg, auto_change = handle_slash("/", "architect")
    assert new_persona == "architect"
    assert keep_going is True
    assert "Empty command" in msg
    assert auto_change is None


# --- Phase 13f: repair-retry y/n prompt ---


def test_prompt_repair_retry_y_returns_y() -> None:
    with patch("builtins.input", return_value="y"):
        assert _prompt_repair_retry() == "y"


def test_prompt_repair_retry_n_returns_n() -> None:
    with patch("builtins.input", return_value="n"):
        assert _prompt_repair_retry() == "n"


def test_prompt_repair_retry_anything_else_treated_as_n() -> None:
    with patch("builtins.input", return_value="maybe"):
        assert _prompt_repair_retry() == "n"


def test_prompt_repair_retry_eof_returns_n() -> None:
    """Piped input ending mid-prompt shouldn't crash; treat as no-retry."""
    with patch("builtins.input", side_effect=EOFError()):
        assert _prompt_repair_retry() == "n"


def test_prompt_repair_retry_is_case_insensitive_and_strips() -> None:
    with patch("builtins.input", return_value="  Y  "):
        assert _prompt_repair_retry() == "y"
