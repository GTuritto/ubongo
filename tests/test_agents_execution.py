from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context, events, skills  # noqa: E402
from ubongo.agents.base import AgentDirectives, AgentInput  # noqa: E402
from ubongo.agents.execution import ExecutionAgent, _extract_fenced_command  # noqa: E402
from ubongo.memory import store, vault  # noqa: E402


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


def _input(message: str = "", *, metadata: dict | None = None) -> AgentInput:
    md = metadata or {}
    return AgentInput(
        message=message,
        history=({"role": "user", "content": message},),
        summary_text=None,
        prior_findings=(),
        directives=AgentDirectives(exec_command=md.get("exec_command")),
    )


def test_metadata_command_runs_and_returns_formatted_result():
    agent = ExecutionAgent()
    result = agent.run(_input(metadata={"exec_command": "echo hi"}), context=None)
    assert result.ok is True
    assert "$ echo hi" in result.text
    assert "exit=0" in result.text
    assert "hi" in result.text
    assert result.metadata["exit_code"] == 0
    assert result.metadata["argv"] == ["echo", "hi"]
    assert agent.composer is False


def test_disallowed_command_marks_execution_refused():
    agent = ExecutionAgent()
    result = agent.run(_input(metadata={"exec_command": "cat /etc/passwd"}), context=None)
    assert result.ok is False
    assert result.error == "execution_refused"
    assert "Refused" in result.text


def test_no_command_anywhere_marks_no_command():
    agent = ExecutionAgent()
    result = agent.run(_input(message="please do something"), context=None)
    assert result.ok is False
    assert result.error == "execution_no_command"


def test_fenced_block_fallback_extracts_command():
    agent = ExecutionAgent()
    msg = "please run this:\n```sh\nls\n```\nthanks"
    result = agent.run(_input(message=msg), context=None)
    assert result.ok is True
    assert "$ ls" in result.text


def test_extract_fenced_command_unit():
    assert _extract_fenced_command("```sh\necho ok\n```") == "echo ok"
    assert _extract_fenced_command("```bash\nls\n```") == "ls"
    assert _extract_fenced_command("no fence") is None
    # multi-line bodies rejected
    assert _extract_fenced_command("```sh\nls\necho hi\n```") is None
    # multiple fences rejected
    assert _extract_fenced_command("```sh\nls\n```\n```sh\npwd\n```") is None


def test_nonzero_exit_marks_ok_false_without_error():
    agent = ExecutionAgent()
    # `false` is allowed and exits 1
    result = agent.run(_input(metadata={"exec_command": "false"}), context=None)
    assert result.ok is False  # exit_code != 0
    assert result.error is None  # it ran cleanly; just nonzero
    assert result.metadata["exit_code"] == 1


def test_truncation_marker_present_for_large_output(monkeypatch):
    """The sandbox caps stdout at 2 KB; the cap marker is the agent's signal
    to the persona that there was more."""
    agent = ExecutionAgent()
    # produce 5000 bytes of stdout via a quoted python expression (no ; allowed)
    # ' ' * 5000 prints 5000 spaces + newline
    result = agent.run(
        _input(metadata={"exec_command": 'python3 -c "print(\'x\' * 5000)"'}),
        context=None,
    )
    assert result.ok is True
    assert "(truncated;" in result.text
