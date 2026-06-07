from __future__ import annotations

import os

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.agents.base import AgentDirectives, AgentInput, AgentResult  # noqa: E402
from ubongo.agents.llm_run import (  # noqa: E402
    call_model_or_none,
    resolve_max_tokens,
    resolve_model,
    run_agent_llm,
)
from ubongo.llm import CompletionResult, LLMError  # noqa: E402

logger = __import__("logging").getLogger("test.llm_run")


def _completion(text: str = "hi") -> CompletionResult:
    return CompletionResult(
        text=text, model="real-model", tokens_in=10, tokens_out=20, latency_ms=5, attempts=1
    )


def _input(directives: dict | None = None) -> AgentInput:
    return AgentInput(
        message="q",
        history=({"role": "user", "content": "q"},),
        summary_text=None,
        prior_findings=(),
        directives=AgentDirectives(**(directives or {})),
    )


# ---- resolution helpers -------------------------------------------------

def test_resolve_model_prefers_override():
    assert resolve_model(_input({"override_model": "ov"}), "default-m") == "ov"
    assert resolve_model(_input(), "default-m") == "default-m"


def test_resolve_max_tokens_prefers_override():
    assert resolve_max_tokens(_input({"max_tokens_override": 200}), 800) == 200
    assert resolve_max_tokens(_input(), 800) == 800


# ---- run_agent_llm: success path ---------------------------------------

def test_run_agent_llm_success_passthrough():
    captured = {}

    def fake_complete(*, system_prompt, messages, model, max_tokens):
        captured.update(system_prompt=system_prompt, model=model, max_tokens=max_tokens)
        return _completion("answer")

    result = run_agent_llm(
        agent_name="demo",
        logger=logger,
        input=_input(),
        system_prompt="SYS",
        messages=[{"role": "user", "content": "q"}],
        default_model="default-m",
        default_max_tokens=800,
        complete_fn=fake_complete,
    )
    assert isinstance(result, AgentResult)
    assert result.ok is True
    assert result.text == "answer"
    assert result.model == "real-model"
    # complete called with keyword args (tests across the suite assert kwargs).
    assert captured == {"system_prompt": "SYS", "model": "default-m", "max_tokens": 800}


def test_run_agent_llm_resolves_overrides_before_calling():
    captured = {}

    def fake_complete(*, system_prompt, messages, model, max_tokens):
        captured.update(model=model, max_tokens=max_tokens)
        return _completion()

    run_agent_llm(
        agent_name="demo",
        logger=logger,
        input=_input({"override_model": "ov", "max_tokens_override": 123}),
        system_prompt="SYS",
        messages=[],
        default_model="default-m",
        default_max_tokens=800,
        complete_fn=fake_complete,
    )
    assert captured == {"model": "ov", "max_tokens": 123}


def test_run_agent_llm_sets_result_metadata_on_success():
    result = run_agent_llm(
        agent_name="demo",
        logger=logger,
        input=_input(),
        system_prompt="SYS",
        messages=[],
        default_model="m",
        default_max_tokens=1,
        complete_fn=lambda **k: _completion(),
        result_metadata={"k": "v"},
    )
    assert result.metadata == {"k": "v"}


# ---- run_agent_llm: error path -----------------------------------------

def test_run_agent_llm_maps_llm_error():
    def boom(**kwargs):
        raise LLMError("boom", cause=RuntimeError("nope"))

    result = run_agent_llm(
        agent_name="demo",
        logger=logger,
        input=_input(),
        system_prompt="SYS",
        messages=[],
        default_model="default-m",
        default_max_tokens=800,
        complete_fn=boom,
    )
    assert result.ok is False
    assert result.error == "demo_llm_error"
    assert result.model == "default-m"
    assert result.text == ""


def test_run_agent_llm_error_text_and_metadata():
    def boom(**kwargs):
        raise LLMError("boom")

    result = run_agent_llm(
        agent_name="demo",
        logger=logger,
        input=_input(),
        system_prompt="SYS",
        messages=[],
        default_model="m",
        default_max_tokens=1,
        complete_fn=boom,
        error_text="friendly failure",
        result_metadata={"r": 1},
    )
    assert result.text == "friendly failure"
    assert result.metadata == {"r": 1}


# ---- run_agent_llm: on_success hook ------------------------------------

def test_run_agent_llm_on_success_overrides_default_result():
    sentinel = AgentResult(
        text="parsed", ok=True, model="m", tokens_in=0, tokens_out=0, latency_ms=0, confidence=0.9
    )

    def fake_complete(**kwargs):
        return _completion("raw json")

    result = run_agent_llm(
        agent_name="demo",
        logger=logger,
        input=_input(),
        system_prompt="SYS",
        messages=[],
        default_model="m",
        default_max_tokens=1,
        complete_fn=fake_complete,
        on_success=lambda completion: sentinel,
    )
    assert result is sentinel


def test_run_agent_llm_on_success_not_called_on_error():
    calls = []

    def boom(**kwargs):
        raise LLMError("boom")

    result = run_agent_llm(
        agent_name="demo",
        logger=logger,
        input=_input(),
        system_prompt="SYS",
        messages=[],
        default_model="m",
        default_max_tokens=1,
        complete_fn=boom,
        on_success=lambda c: calls.append(c),  # type: ignore[arg-type]
    )
    assert result.ok is False
    assert calls == []


# ---- call_model_or_none ------------------------------------------------

def test_call_model_or_none_returns_completion():
    result = call_model_or_none(
        logger=logger,
        error_event="demo_llm_error",
        system_prompt="SYS",
        messages=[],
        model="m",
        max_tokens=1,
        complete_fn=lambda **k: _completion("ok"),
    )
    assert result is not None
    assert result.text == "ok"


def test_call_model_or_none_returns_none_on_error():
    def boom(**kwargs):
        raise LLMError("boom")

    result = call_model_or_none(
        logger=logger,
        error_event="demo_llm_error",
        system_prompt="SYS",
        messages=[],
        model="m",
        max_tokens=1,
        complete_fn=boom,
    )
    assert result is None
