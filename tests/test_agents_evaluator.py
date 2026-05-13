from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context, events, skills  # noqa: E402
from ubongo.agents.base import AgentInput  # noqa: E402
from ubongo.agents.evaluator import EvaluatorAgent, _parse_judgment  # noqa: E402
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


def _completion(text: str) -> CompletionResult:
    return CompletionResult(text=text, model="test-eval", tokens_in=12, tokens_out=8, latency_ms=5, attempts=1)


def _input(candidate: str = "the candidate response", message: str = "explain caching") -> AgentInput:
    findings: tuple[str, ...] = (candidate,) if candidate else ()
    return AgentInput(
        message=message,
        history=({"role": "user", "content": message},),
        summary_text=None,
        prior_findings=findings,
    )


def test_parse_judgment_happy_path():
    out = _parse_judgment('{"confidence": 0.83, "issues": []}')
    assert out == (0.83, [])


def test_parse_judgment_tolerates_code_fence():
    raw = "```json\n{\"confidence\": 0.5, \"issues\": [\"thin reasoning\"]}\n```"
    out = _parse_judgment(raw)
    assert out == (0.5, ["thin reasoning"])


def test_parse_judgment_clamps_above_one():
    out = _parse_judgment('{"confidence": 1.7, "issues": []}')
    assert out is not None
    conf, _ = out
    assert conf == 1.0


def test_parse_judgment_returns_none_on_garbage():
    assert _parse_judgment("sure, sounds good") is None


def test_evaluator_happy_path_returns_confidence():
    agent = EvaluatorAgent()
    with patch(
        "ubongo.agents.evaluator.complete",
        return_value=_completion('{"confidence": 0.83, "issues": []}'),
    ):
        result = agent.run(_input(), context=None)
    assert result.ok is True
    assert result.confidence == 0.83
    assert result.metadata["issues"] == []
    assert "Confidence: 0.83" in result.text


def test_evaluator_parse_error_marks_ok_false():
    agent = EvaluatorAgent()
    with patch(
        "ubongo.agents.evaluator.complete",
        return_value=_completion("sure, sounds good"),
    ):
        result = agent.run(_input(), context=None)
    assert result.ok is False
    assert result.error == "evaluator_parse_error"
    assert result.confidence is None


def test_evaluator_no_candidate_marks_ok_false():
    agent = EvaluatorAgent()
    with patch("ubongo.agents.evaluator.complete") as m:
        result = agent.run(_input(candidate=""), context=None)
    assert result.ok is False
    assert result.error == "evaluator_no_candidate"
    m.assert_not_called()


def test_evaluator_llm_error_marks_ok_false():
    agent = EvaluatorAgent()
    with patch(
        "ubongo.agents.evaluator.complete",
        side_effect=LLMError("boom", cause=RuntimeError("nope")),
    ):
        result = agent.run(_input(), context=None)
    assert result.ok is False
    assert result.error == "evaluator_llm_error"


def test_evaluator_default_model_and_max_tokens_from_settings():
    agent = EvaluatorAgent()
    assert agent.default_model
    assert agent.max_tokens == 400
    assert agent.composer is False
