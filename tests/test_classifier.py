from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import classifier, events, skills  # noqa: E402
from ubongo.classifier import _FALLBACK, Classification, classify  # noqa: E402
from ubongo.llm import CompletionResult, LLMError  # noqa: E402


def _completion(text: str) -> CompletionResult:
    return CompletionResult(text=text, model="test-model", tokens_in=1, tokens_out=1, latency_ms=1, attempts=1)


@pytest.fixture(autouse=True)
def _reset_event_bus():
    events.clear()
    yield
    events.clear()


def _write_skill(root: Path, name: str, description: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"risk: low\n"
        f"reversibility: reversible\n"
        f"---\n"
        f"\n"
        f"body\n",
        encoding="utf-8",
    )


@pytest.fixture
def empty_skills(tmp_path: Path):
    skills.set_skills_dir(tmp_path)
    yield tmp_path
    skills.set_skills_dir(None)


@pytest.fixture
def with_summarize_skill(tmp_path: Path):
    _write_skill(tmp_path, "summarize-conversation", "Recap the conversation in 3-5 sentences.")
    skills.set_skills_dir(tmp_path)
    yield tmp_path
    skills.set_skills_dir(None)


def test_classify_pins_temperature_to_zero() -> None:
    body = ('{"intent":"technical","tone":"neutral","task_type":"question",'
            '"suggested_skill":null,"risk":"low","confidence":0.9}')
    captured: dict = {}

    def _spy(**kwargs):
        captured.update(kwargs)
        return _completion(body)

    with patch("ubongo.classifier.complete", side_effect=_spy):
        classify("design a circuit breaker")
    assert captured.get("temperature") == 0


def test_intent_definitions_in_system_prompt() -> None:
    prompt = classifier._build_system_prompt()
    assert "technical:" in prompt and "coding:" in prompt
    assert "prefer technical over work" in prompt


def test_valid_json_passes_through() -> None:
    body = json.dumps({
        "intent": "technical",
        "tone": "neutral",
        "task_type": "question",
        "suggested_skill": None,
        "risk": "low",
        "confidence": 0.85,
    })
    with patch("ubongo.classifier.complete", return_value=_completion(body)):
        result = classify("design a circuit breaker")
    assert result.intent == "technical"
    assert result.confidence == 0.85
    assert result.suggested_skill is None


def test_code_fences_are_stripped() -> None:
    body = "```json\n" + json.dumps({
        "intent": "casual",
        "tone": "tired",
        "task_type": "chat",
        "suggested_skill": None,
        "risk": "low",
        "confidence": 0.9,
    }) + "\n```"
    with patch("ubongo.classifier.complete", return_value=_completion(body)):
        result = classify("ugh today sucked")
    assert result.intent == "casual"
    assert result.tone == "tired"


def test_out_of_vocab_intent_falls_back() -> None:
    body = json.dumps({
        "intent": "philosophy",
        "tone": "neutral",
        "task_type": "question",
        "suggested_skill": None,
        "risk": "low",
        "confidence": 0.9,
    })
    with patch("ubongo.classifier.complete", return_value=_completion(body)):
        result = classify("what is meaning?")
    assert result == _FALLBACK


def test_malformed_json_falls_back() -> None:
    with patch("ubongo.classifier.complete", return_value=_completion("not even close to JSON")):
        result = classify("hello")
    assert result == _FALLBACK


def test_missing_required_field_falls_back() -> None:
    body = json.dumps({"intent": "technical"})  # missing everything else
    with patch("ubongo.classifier.complete", return_value=_completion(body)):
        result = classify("hello")
    assert result == _FALLBACK


def test_confidence_clamped_to_unit_interval() -> None:
    body = json.dumps({
        "intent": "casual",
        "tone": "neutral",
        "task_type": "chat",
        "suggested_skill": None,
        "risk": "low",
        "confidence": 1.5,
    })
    with patch("ubongo.classifier.complete", return_value=_completion(body)):
        result = classify("hi")
    assert result.confidence == 1.0


def test_llm_error_falls_back() -> None:
    with patch("ubongo.classifier.complete", side_effect=LLMError("boom", cause=RuntimeError("nope"))):
        result = classify("hello")
    assert result == _FALLBACK


def test_before_and_after_classify_events_dispatched() -> None:
    seen: list[tuple[str, dict]] = []
    events.register("before_classify", lambda p: seen.append(("before", p)))
    events.register("after_classify", lambda p: seen.append(("after", p)))

    body = json.dumps({
        "intent": "casual",
        "tone": "neutral",
        "task_type": "chat",
        "suggested_skill": None,
        "risk": "low",
        "confidence": 0.9,
    })
    with patch("ubongo.classifier.complete", return_value=_completion(body)):
        classify("hi there")

    names = [name for name, _ in seen]
    assert names == ["before", "after"]
    assert seen[0][1] == {"message_length": len("hi there")}
    assert seen[1][1]["fallback"] is False


def test_after_classify_marks_fallback_true_on_error() -> None:
    seen: list[dict] = []
    events.register("after_classify", seen.append)
    with patch("ubongo.classifier.complete", return_value=_completion("garbage")):
        classify("hi")
    assert seen[0]["fallback"] is True


def test_system_prompt_omits_skills_block_when_registry_empty(empty_skills: Path) -> None:
    prompt = classifier._build_system_prompt()
    assert "## Available skills" not in prompt
    assert "suggested_skill: null" in prompt


def test_system_prompt_lists_registered_skills(with_summarize_skill: Path) -> None:
    prompt = classifier._build_system_prompt()
    assert "## Available skills" in prompt
    assert "- summarize-conversation — Recap the conversation in 3-5 sentences." in prompt
    assert "one of the listed skill names below, or null" in prompt


def test_known_skill_passes_through(with_summarize_skill: Path) -> None:
    body = json.dumps({
        "intent": "casual",
        "tone": "neutral",
        "task_type": "command",
        "suggested_skill": "summarize-conversation",
        "risk": "low",
        "confidence": 0.8,
    })
    with patch("ubongo.classifier.complete", return_value=_completion(body)):
        result = classify("can you wrap this up for me")
    assert result.suggested_skill == "summarize-conversation"


def test_unknown_skill_coerced_to_none_and_logs(
    with_summarize_skill: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="ubongo.classifier")
    body = json.dumps({
        "intent": "casual",
        "tone": "neutral",
        "task_type": "command",
        "suggested_skill": "not-a-real-skill",
        "risk": "low",
        "confidence": 0.8,
    })
    with patch("ubongo.classifier.complete", return_value=_completion(body)):
        result = classify("hi")
    assert result.suggested_skill is None
    assert result.intent == "casual"
    unknown_logs = [r for r in caplog.records if r.message == "classify_unknown_skill"]
    assert len(unknown_logs) == 1
