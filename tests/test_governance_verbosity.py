"""Verbosity per domain (v0.5 phase 07): the manual-first length knob. Covers
the resolver (task_type -> intent -> default), the one-shot normalize, the
directive text, and the threading into the composer persona's system prompt."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.agents.base import AgentDirectives, AgentInput, AgentResult  # noqa: E402
from ubongo.governance import verbosity  # noqa: E402

_BLOCK = {"verbosity": {"default": "normal",
                        "levels": {"technical": "deep", "casual": "terse", "command": "terse"}}}


def _patch_block(monkeypatch, block=_BLOCK):
    monkeypatch.setattr(verbosity, "load_governance", lambda: block)


def _cls(task_type=None, intent=None):
    return SimpleNamespace(task_type=task_type, intent=intent)


# --- resolver --------------------------------------------------------------


def test_level_for_prefers_task_type(monkeypatch):
    _patch_block(monkeypatch)
    assert verbosity.level_for(_cls(task_type="technical", intent="casual")) == "deep"


def test_level_for_falls_back_to_intent(monkeypatch):
    _patch_block(monkeypatch)
    assert verbosity.level_for(_cls(task_type="unmapped", intent="casual")) == "terse"


def test_level_for_default_when_unmapped(monkeypatch):
    _patch_block(monkeypatch)
    assert verbosity.level_for(_cls(task_type="zzz", intent="yyy")) == "normal"


def test_missing_block_is_normal(monkeypatch):
    monkeypatch.setattr(verbosity, "load_governance", lambda: {})
    assert verbosity.default_level() == "normal"
    assert verbosity.levels_map() == {}
    assert verbosity.level_for(_cls(task_type="technical")) == "normal"


def test_invalid_level_in_config_ignored(monkeypatch):
    _patch_block(monkeypatch, {"verbosity": {"default": "loud", "levels": {"x": "screaming"}}})
    assert verbosity.default_level() == "normal"   # 'loud' is not a known level
    assert verbosity.levels_map() == {}            # 'screaming' dropped


# --- normalize + directive text -------------------------------------------


def test_normalize():
    assert verbosity.normalize("DEEP") == "deep"
    assert verbosity.normalize("terse") == "terse"
    assert verbosity.normalize("bogus") is None
    assert verbosity.normalize(None) is None


def test_directive_text_only_terse_and_deep_add_a_line():
    assert verbosity.directive_text("terse").startswith("## Response length")
    assert "thorough" in verbosity.directive_text("deep")
    assert verbosity.directive_text("normal") is None
    assert verbosity.directive_text(None) is None


# --- threading into the persona prompt ------------------------------------


def _capture_persona_prompt(monkeypatch, level):
    from ubongo.agents import personas

    captured = {}

    def _fake_llm(*, system_prompt, **kwargs):
        captured["prompt"] = system_prompt
        return AgentResult(text="ok", ok=True, model="m", tokens_in=1, tokens_out=1, latency_ms=1)

    monkeypatch.setattr(personas, "run_agent_llm", _fake_llm)
    agent = personas.ArchitectPersona()
    inp = AgentInput(message="hi", history=(), summary_text=None, prior_findings=(),
                     directives=AgentDirectives(verbosity=level))
    agent.run(inp, context=None)
    return captured["prompt"]


def test_terse_directive_threads_into_prompt(monkeypatch):
    prompt = _capture_persona_prompt(monkeypatch, "terse")
    assert "## Response length" in prompt and "Be terse" in prompt


def test_normal_directive_adds_nothing(monkeypatch):
    prompt = _capture_persona_prompt(monkeypatch, None)
    assert "## Response length" not in prompt
