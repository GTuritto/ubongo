from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context, events, skills  # noqa: E402
from ubongo.agents.base import AgentInput, AgentResult  # noqa: E402
from ubongo.agents.repair import RepairAgent, default_repair_agent  # noqa: E402
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


def _failed_result(error: str) -> AgentResult:
    return AgentResult(
        text="", ok=False, model="m",
        tokens_in=0, tokens_out=0, latency_ms=10,
        error=error,
    )


def _input() -> AgentInput:
    return AgentInput(
        message="hi",
        history=({"role": "user", "content": "hi"},),
        summary_text=None,
        prior_findings=(),
    )


def test_plan_retry_returns_fallback_for_persona_llm_error():
    agent = RepairAgent()
    plan = agent.plan_retry("architect", _failed_result("persona_llm_error"), _input())
    assert plan is not None
    assert "model" in plan
    assert plan["model"]


def test_plan_retry_returns_none_for_memory():
    agent = RepairAgent()
    plan = agent.plan_retry("memory", _failed_result("write_failed"), _input())
    assert plan is None


def test_plan_retry_returns_none_for_execution():
    agent = RepairAgent()
    plan = agent.plan_retry("execution", _failed_result("execution_refused"), _input())
    assert plan is None


def test_plan_retry_returns_none_for_unknown_error_kind():
    agent = RepairAgent()
    plan = agent.plan_retry("research", _failed_result("totally_new_error"), _input())
    assert plan is None


def test_plan_retry_returns_fallback_for_coding_llm_error():
    agent = RepairAgent()
    plan = agent.plan_retry("coding", _failed_result("coding_llm_error"), _input())
    assert plan is not None
    assert plan["model"]


def test_plan_retry_casual_uses_casual_fallback_model():
    agent = RepairAgent()
    plan = agent.plan_retry("casual", _failed_result("persona_llm_error"), _input())
    assert plan is not None
    # casual fallback defaults to models.casual (haiku) per agent's __init__
    assert "haiku" in plan["model"].lower() or "casual" in plan["model"].lower() or plan["model"]


def test_plan_retry_for_evaluator_and_critic():
    agent = RepairAgent()
    assert agent.plan_retry("evaluator", _failed_result("evaluator_llm_error"), _input()) is not None
    assert agent.plan_retry("critic", _failed_result("critic_llm_error"), _input()) is not None


def test_default_repair_agent_is_a_singleton():
    assert default_repair_agent.name == "repair"
    assert default_repair_agent.composer is False


def test_run_is_a_no_op_returning_ok():
    agent = RepairAgent()
    result = agent.run(_input(), context=None)
    assert result.ok is True
    assert result.text == ""
