from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context, events, skills  # noqa: E402
from ubongo.memory import store, vault  # noqa: E402
from ubongo.repl import _parse_exec_command, _render_exec  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")
    vault.set_vault_root(tmp_path / "vault")
    skills.set_skills_dir(None)
    skills.reload()
    context.reload()
    events.clear()
    yield
    events.clear()
    skills.set_skills_dir(None)
    skills.reload()
    context.reload()
    store.set_db_path(None)
    vault.set_vault_root(None)


# --- parser ---


def test_parse_exec_returns_body():
    assert _parse_exec_command("/exec echo hello") == "echo hello"


def test_parse_exec_preserves_quotes_and_spaces():
    assert _parse_exec_command('/exec echo "hello world"') == 'echo "hello world"'


def test_parse_exec_returns_none_when_empty():
    assert _parse_exec_command("/exec") is None
    assert _parse_exec_command("/exec   ") is None


def test_parse_exec_returns_none_for_other_commands():
    assert _parse_exec_command("/trace") is None
    assert _parse_exec_command("/queue") is None


# --- renderer ---


def test_render_exec_happy_path():
    out = _render_exec("echo hi")
    assert "$ echo hi" in out
    assert "exit=0" in out
    assert "ms)" in out  # latency suffix
    assert "stdout:" in out
    assert "hi" in out


def test_render_exec_refused_for_disallowed_program():
    out = _render_exec("rm -rf /")
    assert out.startswith("Refused:")
    assert "allowlist" in out


def test_render_exec_refused_for_metachar():
    out = _render_exec("ls; cat /etc/passwd")
    assert out.startswith("Refused:")
    assert "metacharacter" in out


def test_render_exec_does_not_create_workflow_run():
    """/exec is debug-only; it must not create a workflow_runs row."""
    _render_exec("echo audit")
    rows = store.connection().execute("SELECT COUNT(*) AS c FROM workflow_runs").fetchone()
    assert rows["c"] == 0
