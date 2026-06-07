from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context, events, skills  # noqa: E402
from ubongo.agents.base import AgentDirectives, AgentInput  # noqa: E402
from ubongo.agents.critic import CriticAgent, _extract_evaluator_issues  # noqa: E402
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
    return CompletionResult(text=text, model="test-critic", tokens_in=18, tokens_out=22, latency_ms=12, attempts=1)


def _input(prior: tuple[str, ...], message: str = "should we use microservices") -> AgentInput:
    return AgentInput(
        message=message,
        history=({"role": "user", "content": message},),
        summary_text=None,
        prior_findings=prior,
    )


def test_critic_happy_path_returns_bullets():
    agent = CriticAgent()
    response = "- The cost argument is hand-waved.\n- The team-size assumption is unstated."
    with patch("ubongo.agents.critic.complete", return_value=_completion(response)) as m:
        result = agent.run(_input(prior=("the candidate response",)), context=None)
    assert result.ok is True
    assert result.text == response
    assert agent.composer is False
    m.assert_called_once()


def test_critic_no_candidate_marks_ok_false():
    agent = CriticAgent()
    with patch("ubongo.agents.critic.complete") as m:
        result = agent.run(_input(prior=()), context=None)
    assert result.ok is False
    assert result.error == "critic_no_candidate"
    m.assert_not_called()


def test_critic_sees_evaluator_findings_in_prompt():
    agent = CriticAgent()
    prior = (
        "Confidence: 0.45. Issues: thin reasoning.",
        "Architect's response: we should use microservices.",
    )
    with patch("ubongo.agents.critic.complete", return_value=_completion("- bullet")) as m:
        agent.run(_input(prior=prior), context=None)
    sys_prompt = m.call_args.kwargs["system_prompt"]
    assert "## Evaluator flagged issues" in sys_prompt
    assert "thin reasoning" in sys_prompt


def test_critic_llm_error_marks_ok_false():
    agent = CriticAgent()
    with patch(
        "ubongo.agents.critic.complete",
        side_effect=LLMError("boom", cause=RuntimeError("nope")),
    ):
        result = agent.run(_input(prior=("the candidate response",)), context=None)
    assert result.ok is False
    assert result.error == "critic_llm_error"


def test_extract_evaluator_issues_only_matches_eval_prefix():
    findings = (
        "some random research finding",
        "Confidence: 0.55. Issues: missing context.",
        "the candidate response itself",  # last is the candidate, skipped
    )
    out = _extract_evaluator_issues(findings)
    assert out is not None
    assert out.startswith("Confidence:")


def test_critic_appends_repair_prompt_hint_and_max_tokens_override():
    """Phase 13b: Repair hint reaches the critic's system prompt."""
    agent = CriticAgent()
    inp = AgentInput(
        message="x", history=({"role": "user", "content": "x"},),
        summary_text=None, prior_findings=("the candidate response",),
        directives=AgentDirectives(repair_prompt_hint="Be terse.", max_tokens_override=200),
    )
    with patch("ubongo.agents.critic.complete", return_value=_completion("- bullet")) as m:
        agent.run(inp, context=None)
    sp = m.call_args.kwargs["system_prompt"]
    assert "## Repair guidance" in sp
    assert "Be terse." in sp
    assert m.call_args.kwargs["max_tokens"] == 200
