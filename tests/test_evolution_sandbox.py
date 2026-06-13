from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import evaluation
from ubongo.evolution import sandbox  # noqa: E402
from ubongo.evolution.sandbox import CallBudget  # noqa: E402


class _Fake:
    def __init__(self, text: str) -> None:
        self.text = text
        self.model = "fake"
        self.tokens_in = 10
        self.tokens_out = 20
        self.latency_ms = 42
        self.attempts = 1


def _is_judge(system_prompt: str) -> bool:
    return "evaluation judge" in system_prompt.lower()


@pytest.fixture
def fake_llm(monkeypatch):
    """Generation calls return a canned response; judge calls return a fixed
    judgment. Captures every system prompt for assembly assertions."""
    seen: dict[str, list] = {"gen_prompts": [], "judge_prompts": [], "judgment": [
        {"quality": 0.8, "hallucination": 0.2, "would_user_correct": False}
    ]}

    def _fake(system_prompt, messages, model, max_tokens):
        if _is_judge(system_prompt):
            seen["judge_prompts"].append(system_prompt)
            return _Fake(json.dumps(seen["judgment"][0]))
        seen["gen_prompts"].append(system_prompt)
        return _Fake("a generated response")

    monkeypatch.setattr(sandbox, "complete", _fake)
    return seen


_SAMPLES = {
    "version": "test-v1",
    "conversations": [
        {"id": "a1", "persona_affinity": "architect",
         "turns": [{"role": "user", "content": "design question?"}]},
        {"id": "g1", "persona_affinity": None,
         "turns": [{"role": "user", "content": "general question?"}]},
        {"id": "o1", "persona_affinity": "operator",
         "turns": [{"role": "user", "content": "ops question?"}]},
    ],
}


def _variant(lineage_id: int, text: str = "You are the architect."):
    return {"id": lineage_id, "variant_text": text}


# --- sample selection -------------------------------------------------------

def test_select_samples_affinity_plus_general() -> None:
    sel = sandbox.select_samples(_SAMPLES, "persona:architect", 10)
    ids = [c["id"] for c in sel]
    assert ids == ["a1", "g1"]  # architect + general, not operator


def test_select_samples_truncates_to_limit() -> None:
    sel = sandbox.select_samples(_SAMPLES, "persona:architect", 1)
    assert len(sel) == 1


def test_select_samples_fallback_to_all_when_no_match() -> None:
    only_ops = {"version": "v", "conversations": [_SAMPLES["conversations"][2]]}
    sel = sandbox.select_samples(only_ops, "persona:architect", 10)
    assert [c["id"] for c in sel] == ["o1"]  # fell back to the full set


# --- CallBudget -------------------------------------------------------------

def test_call_budget_can_afford_and_spend() -> None:
    b = CallBudget(4)
    assert b.can_afford(4)
    assert not b.can_afford(5)
    b.spend(2)
    assert b.remaining() == 2
    assert b.can_afford(2)
    assert not b.can_afford(3)


# --- evaluate_variant -------------------------------------------------------

def test_evaluate_variant_aggregates(monkeypatch) -> None:
    # Alternate would_user_correct across the two samples -> corr = 0.5.
    judgments = [
        {"quality": 0.8, "hallucination": 0.2, "would_user_correct": True},
        {"quality": 0.6, "hallucination": 0.4, "would_user_correct": False},
    ]
    calls = {"i": 0}

    def _fake(system_prompt, messages, model, max_tokens):
        if "evaluation judge" in system_prompt.lower():
            j = judgments[calls["i"] % len(judgments)]
            calls["i"] += 1
            return _Fake(json.dumps(j))
        return _Fake("resp")

    monkeypatch.setattr(sandbox, "complete", _fake)
    samples = _SAMPLES["conversations"][:2]
    m = sandbox.evaluate_variant(_variant(1), samples, gen_model="g", judge_model="j", budget=CallBudget(100))
    assert m is not None
    assert abs(m.success_rate - 0.7) < 1e-9
    assert abs(m.hallucination_rate - 0.3) < 1e-9
    assert abs(m.user_correction_rate - 0.5) < 1e-9
    assert m.lineage_id == 1


def test_evaluate_variant_skipped_when_budget_too_small(fake_llm) -> None:
    samples = _SAMPLES["conversations"][:2]  # needs 2*2 = 4 calls
    m = sandbox.evaluate_variant(_variant(1), samples, gen_model="g", judge_model="j", budget=CallBudget(3))
    assert m is None  # all-or-nothing


def test_prompt_assembly_uses_ubongo_plus_variant_no_skill(fake_llm) -> None:
    samples = _SAMPLES["conversations"][:1]
    sandbox.evaluate_variant(_variant(1, "ARCHITECT_VARIANT_BODY"), samples,
                             gen_model="g", judge_model="j", budget=CallBudget(100))
    gen_prompt = fake_llm["gen_prompts"][0]
    assert "ARCHITECT_VARIANT_BODY" in gen_prompt
    assert "## Active Skill" not in gen_prompt
    assert "## Agent Role" not in gen_prompt


def test_bad_variant_drives_hallucination_up(fake_llm) -> None:
    fake_llm["judgment"][0] = {"quality": 0.2, "hallucination": 0.95, "would_user_correct": True}
    samples = _SAMPLES["conversations"][:2]
    m = sandbox.evaluate_variant(_variant(9), samples, gen_model="g", judge_model="j", budget=CallBudget(100))
    assert m is not None
    assert m.hallucination_rate > 0.9
    assert m.success_rate < 0.3
    assert m.user_correction_rate == 1.0


# --- judge parser -----------------------------------------------------------

def test_judge_parser_tolerates_code_fence() -> None:
    raw = '```json\n{"quality": 0.7, "hallucination": 0.1, "would_user_correct": false}\n```'
    parsed = evaluation.parse_judgment(raw)
    assert parsed == (0.7, 0.1, False)


def test_judge_parser_extracts_object_from_prose() -> None:
    # Judges sometimes wrap the JSON in explanation despite the rubric.
    raw = (
        "Here is my assessment of the response:\n"
        '{"quality": 0.6, "hallucination": 0.3, "would_user_correct": true}\n'
        "The response was mostly fine but a bit vague."
    )
    assert evaluation.parse_judgment(raw) == (0.6, 0.3, True)


def test_judge_parser_rejects_malformed() -> None:
    assert evaluation.parse_judgment("not json") is None
    assert evaluation.parse_judgment('{"quality": "high"}') is None


def test_judge_parser_clamps() -> None:
    parsed = evaluation.parse_judgment('{"quality": 1.5, "hallucination": -0.2, "would_user_correct": true}')
    assert parsed == (1.0, 0.0, True)


# --- evaluate_target --------------------------------------------------------

def test_evaluate_target_budget_skips_some(fake_llm) -> None:
    rows = [_variant(1), _variant(2), _variant(3)]
    # 1 sample/variant -> 2 calls each. Budget 4 -> 2 variants fit, 1 skipped.
    result = sandbox.evaluate_target(
        rows, "persona:architect", sample_set=_SAMPLES,
        samples_per_eval=1, budget=CallBudget(4),
    )
    assert result.evaluated == 2
    assert result.skipped == 1
    assert result.total_variants == 3
    assert result.sample_set_version == "test-v1"
    assert len(result.cohort) == 2
