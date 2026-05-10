from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import classifier, events  # noqa: E402
from ubongo.classifier import _FALLBACK, Classification, classify  # noqa: E402
from ubongo.llm import CompletionResult, LLMError  # noqa: E402


def _completion(text: str) -> CompletionResult:
    return CompletionResult(text=text, model="test-model", tokens_in=1, tokens_out=1, latency_ms=1, attempts=1)


@pytest.fixture(autouse=True)
def _reset_event_bus():
    events.clear()
    yield
    events.clear()


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
