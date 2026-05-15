from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context, events, skills  # noqa: E402
from ubongo.agents.base import AgentInput  # noqa: E402
from ubongo.agents.evaluator import EvaluatorAgent, _parse_agree, _parse_judgment, _parse_ranking  # noqa: E402
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


def test_evaluator_appends_repair_prompt_hint_and_max_tokens_override():
    """Phase 13b: PARSE_ERROR retry passes a stricter-schema hint and
    (when paired with smaller-model) a max_tokens cap."""
    agent = EvaluatorAgent()
    inp = AgentInput(
        message="explain caching",
        history=({"role": "user", "content": "explain caching"},),
        summary_text=None,
        prior_findings=("the candidate response",),
        metadata={"repair_prompt_hint": "JSON ONLY.", "max_tokens_override": 200},
    )
    with patch(
        "ubongo.agents.evaluator.complete",
        return_value=_completion('{"confidence": 0.7, "issues": []}'),
    ) as m:
        agent.run(inp, context=None)
    sp = m.call_args.kwargs["system_prompt"]
    assert "## Repair guidance" in sp
    assert "JSON ONLY." in sp
    assert m.call_args.kwargs["max_tokens"] == 200


# --- Phase 12b: rank() ---


def _rank_completion(text: str) -> CompletionResult:
    return CompletionResult(text=text, model="test-eval", tokens_in=30, tokens_out=20, latency_ms=10, attempts=1)


def test_parse_ranking_happy_path():
    raw = '{"winner_index": 1, "reason": "B is more complete", "scores": [{"index": 0, "score": 0.6, "note": "ok"}, {"index": 1, "score": 0.85, "note": "good"}]}'
    out = _parse_ranking(raw, n_candidates=2)
    assert out is not None
    idx, reason, scores = out
    assert idx == 1
    assert "complete" in reason
    assert len(scores) == 2 and scores[1]["score"] == 0.85


def test_parse_ranking_rejects_out_of_range_index():
    raw = '{"winner_index": 5, "reason": "x", "scores": []}'
    assert _parse_ranking(raw, n_candidates=2) is None


def test_parse_ranking_rejects_garbage():
    assert _parse_ranking("not json", n_candidates=2) is None


def test_rank_happy_path():
    agent = EvaluatorAgent()
    raw = '{"winner_index": 0, "reason": "concise and correct", "scores": [{"index": 0, "score": 0.9, "note": "tight"}]}'
    with patch("ubongo.agents.evaluator.complete", return_value=_rank_completion(raw)) as m:
        out = agent.rank(
            "what is 2+2",
            [("coding", "4"), ("architect", "The answer is four; here is a proof...")],
        )
    assert out is not None
    assert out["winner"] == "coding"
    assert out["winner_index"] == 0
    assert "concise" in out["reason"]
    m.assert_called_once()


def test_rank_returns_none_on_parse_error():
    agent = EvaluatorAgent()
    with patch("ubongo.agents.evaluator.complete", return_value=_rank_completion("not json")):
        assert agent.rank("q", [("a", "x"), ("b", "y")]) is None


def test_rank_returns_none_on_llm_error():
    agent = EvaluatorAgent()
    with patch("ubongo.agents.evaluator.complete", side_effect=LLMError("boom", cause=RuntimeError("x"))):
        assert agent.rank("q", [("a", "x"), ("b", "y")]) is None


def test_rank_returns_none_on_empty_candidates():
    agent = EvaluatorAgent()
    assert agent.rank("q", []) is None


def test_rank_truncates_large_candidates_in_prompt():
    """Per-candidate truncation prevents prompt bloat. Verify by inspecting
    captured system_prompt."""
    agent = EvaluatorAgent()
    huge = "x" * 5000
    raw = '{"winner_index": 0, "reason": "ok", "scores": []}'
    with patch("ubongo.agents.evaluator.complete", return_value=_rank_completion(raw)) as m:
        agent.rank("q", [("a", huge), ("b", "small")])
    sys_prompt = m.call_args.kwargs["system_prompt"]
    # Truncation marker present; full 5000 'x' run not in the prompt.
    assert "…" in sys_prompt
    assert "x" * 2000 not in sys_prompt
    # The small candidate is intact.
    assert "small" in sys_prompt


def test_rank_honors_override_model():
    agent = EvaluatorAgent()
    raw = '{"winner_index": 0, "reason": "ok", "scores": []}'
    with patch("ubongo.agents.evaluator.complete", return_value=_rank_completion(raw)) as m:
        agent.rank("q", [("a", "x"), ("b", "y")], override_model="fallback-model")
    assert m.call_args.kwargs["model"] == "fallback-model"


# --- Phase 12e: agree() ---


def test_parse_agree_true_and_false_and_garbage():
    assert _parse_agree('{"agree": true, "reason": "same answer"}') is True
    assert _parse_agree('{"agree": false, "reason": "different"}') is False
    assert _parse_agree('{"agree": "yes"}') is None  # not a bool
    assert _parse_agree("not json") is None


def test_agree_happy_path():
    agent = EvaluatorAgent()
    with patch("ubongo.agents.evaluator.complete", return_value=_rank_completion('{"agree": true, "reason": "x"}')):
        assert agent.agree("q", "answer is 4", "the answer is four") is True


def test_agree_returns_none_on_empty_input():
    agent = EvaluatorAgent()
    assert agent.agree("q", "", "y") is None
    assert agent.agree("q", "x", "  ") is None


def test_agree_returns_none_on_llm_error():
    agent = EvaluatorAgent()
    with patch("ubongo.agents.evaluator.complete", side_effect=LLMError("boom", cause=RuntimeError("x"))):
        assert agent.agree("q", "a", "b") is None
