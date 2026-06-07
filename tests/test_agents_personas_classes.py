from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context, events, skills  # noqa: E402
from ubongo.agents.base import AgentDirectives, AgentInput  # noqa: E402
from ubongo.agents.personas import (  # noqa: E402
    ArchitectPersona,
    BasePersonaAgent,
    CasualPersona,
    OperatorPersona,
    VALID_PERSONAS,
)
from ubongo.llm import CompletionResult  # noqa: E402
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


def _completion(text: str = "response text") -> CompletionResult:
    return CompletionResult(text=text, model="m", tokens_in=10, tokens_out=10, latency_ms=5, attempts=1)


def _input() -> AgentInput:
    return AgentInput(
        message="hi",
        history=({"role": "user", "content": "hi"},),
        summary_text=None,
        prior_findings=(),
    )


def test_architect_persona_binds_bare_name_and_model():
    agent = ArchitectPersona()
    assert agent.name == "architect"
    assert agent.persona_name == "architect"
    assert agent.composer is True
    assert agent.default_model  # resolved from settings.yaml


def test_operator_persona_binds_bare_name():
    agent = OperatorPersona()
    assert agent.name == "operator"
    assert agent.composer is True


def test_casual_persona_binds_bare_name():
    agent = CasualPersona()
    assert agent.name == "casual"
    assert agent.composer is True


def test_base_persona_agent_requires_subclass_name():
    with pytest.raises(TypeError):
        BasePersonaAgent()


def test_persona_run_inherited_from_base_calls_llm():
    agent = ArchitectPersona()
    with patch("ubongo.agents.personas.complete", return_value=_completion("architect reply")) as m:
        result = agent.run(_input(), context=None)
    assert result.ok is True
    assert result.text == "architect reply"
    # complete() was called with the architect's model
    assert m.call_args.kwargs["model"] == agent.default_model


def test_valid_personas_tuple_matches_subclasses():
    assert set(VALID_PERSONAS) == {"architect", "operator", "casual"}


def test_persona_appends_repair_prompt_hint_from_metadata():
    """Phase 13b: a same-model repair retry passes a prompt_hint addendum
    via input.metadata['repair_prompt_hint']; the persona appends it under
    a `## Repair guidance` section in the system prompt."""
    agent = ArchitectPersona()
    inp = AgentInput(
        message="hi",
        history=({"role": "user", "content": "hi"},),
        summary_text=None,
        prior_findings=(),
        directives=AgentDirectives(repair_prompt_hint="Be brief and JSON-only."),
    )
    with patch("ubongo.agents.personas.complete", return_value=_completion("ok")) as m:
        agent.run(inp, context=None)
    sp = m.call_args.kwargs["system_prompt"]
    assert "## Repair guidance" in sp
    assert "Be brief and JSON-only." in sp


def test_persona_max_tokens_override_applies():
    """Phase 13b: smaller-model retry caps max_tokens via metadata."""
    agent = ArchitectPersona()
    inp = AgentInput(
        message="hi",
        history=({"role": "user", "content": "hi"},),
        summary_text=None,
        prior_findings=(),
        directives=AgentDirectives(max_tokens_override=200),
    )
    with patch("ubongo.agents.personas.complete", return_value=_completion("ok")) as m:
        agent.run(inp, context=None)
    assert m.call_args.kwargs["max_tokens"] == 200
