from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context, events, skills  # noqa: E402
from ubongo.agents.base import AgentInput  # noqa: E402
from ubongo.agents.coding import CodingAgent  # noqa: E402
from ubongo.llm import CompletionResult, LLMError  # noqa: E402
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


def _completion(text: str = "def f(): pass") -> CompletionResult:
    return CompletionResult(text=text, model="test-coding", tokens_in=40, tokens_out=80, latency_ms=20, attempts=1)


def _input(message: str = "write a function that reverses a list",
           prior: tuple[str, ...] = ()) -> AgentInput:
    return AgentInput(
        message=message,
        history=({"role": "user", "content": message},),
        summary_text=None,
        prior_findings=prior,
    )


def test_coding_happy_path_returns_code():
    agent = CodingAgent()
    code = "def reverse_list(lst: list) -> list:\n    return lst[::-1]"
    with patch("ubongo.agents.coding.complete", return_value=_completion(code)) as m:
        result = agent.run(_input(), context=None)
    assert result.ok is True
    assert result.text == code
    assert agent.composer is True
    m.assert_called_once()


def test_coding_llm_error_marks_ok_false():
    agent = CodingAgent()
    with patch(
        "ubongo.agents.coding.complete",
        side_effect=LLMError("boom", cause=RuntimeError("nope")),
    ):
        result = agent.run(_input(), context=None)
    assert result.ok is False
    assert result.error == "coding_llm_error"


def test_coding_default_model_and_max_tokens_from_settings():
    agent = CodingAgent()
    assert agent.default_model  # resolved from models.coding
    assert agent.max_tokens == 2048


def test_coding_system_prompt_includes_coding_stanza():
    agent = CodingAgent()
    with patch("ubongo.agents.coding.complete", return_value=_completion()) as m:
        agent.run(_input(), context=None)
    sys_prompt = m.call_args.kwargs["system_prompt"]
    assert "You are the Coding Agent" in sys_prompt
    assert "type hints" in sys_prompt
    assert "usage example" in sys_prompt


def test_coding_threads_prior_findings():
    agent = CodingAgent()
    with patch("ubongo.agents.coding.complete", return_value=_completion()) as m:
        agent.run(_input(prior=("first finding", "second finding")), context=None)
    sys_prompt = m.call_args.kwargs["system_prompt"]
    assert "## Prior agent findings #1" in sys_prompt
    assert "first finding" in sys_prompt
    assert "## Prior agent findings #2" in sys_prompt
    assert "second finding" in sys_prompt


def test_coding_honors_override_model_from_metadata():
    agent = CodingAgent()
    inp = AgentInput(
        message="x", history=({"role": "user", "content": "x"},),
        summary_text=None, prior_findings=(), metadata={"override_model": "fallback-m"},
    )
    with patch("ubongo.agents.coding.complete", return_value=_completion()) as m:
        agent.run(inp, context=None)
    assert m.call_args.kwargs["model"] == "fallback-m"
