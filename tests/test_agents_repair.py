from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context, events, skills  # noqa: E402
from ubongo.agents.base import AgentInput, AgentResult  # noqa: E402
from ubongo.agents.repair import (  # noqa: E402
    FailureKind,
    RepairAgent,
    _classify_failure,
    default_repair_agent,
)
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


def _failed_result(error: str | None) -> AgentResult:
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


# ---------- _classify_failure (Phase 13a taxonomy) ----------

def test_classify_persona_llm_error_is_model_error():
    assert _classify_failure("architect", "persona_llm_error") is FailureKind.MODEL_ERROR


def test_classify_coding_llm_error_is_model_error():
    assert _classify_failure("coding", "coding_llm_error") is FailureKind.MODEL_ERROR


def test_classify_evaluator_parse_error_is_parse_error():
    assert _classify_failure("evaluator", "evaluator_parse_error") is FailureKind.PARSE_ERROR


def test_classify_evaluator_rank_parse_error_is_parse_error():
    assert _classify_failure("evaluator", "evaluator_rank_parse_error") is FailureKind.PARSE_ERROR


def test_classify_classifier_parse_error_is_parse_error():
    assert _classify_failure("classifier", "classifier_parse_error") is FailureKind.PARSE_ERROR


def test_classify_critic_no_candidate_is_precondition_missing():
    # Option A: critic_no_candidate belongs in PRECONDITION_MISSING (input
    # contract not met) so its ladder leads with peer replacement instead of
    # wasting a variant-prompt retry. Smoke 12.4 regression.
    assert _classify_failure("critic", "critic_no_candidate") is FailureKind.PRECONDITION_MISSING


def test_classify_memory_missing_input_is_precondition_missing():
    assert _classify_failure("memory", "memory_missing_input") is FailureKind.PRECONDITION_MISSING


def test_classify_execution_no_command_is_precondition_missing():
    assert _classify_failure("execution", "execution_no_command") is FailureKind.PRECONDITION_MISSING


def test_classify_execution_refused_is_unrecoverable():
    # Sandbox refusal is by design; never retry.
    assert _classify_failure("execution", "execution_refused") is FailureKind.UNRECOVERABLE


def test_classify_memory_with_other_error_is_unrecoverable():
    # Memory writes (except memory_missing_input) need Phase-21 DB rollback,
    # not Phase-13 retry. Catch-all guard.
    assert _classify_failure("memory", "write_failed") is FailureKind.UNRECOVERABLE


def test_classify_none_error_is_model_error():
    # Agent crashed without setting an explicit error code; runner caught
    # the exception. Treat as a generic transport bug worth one model swap.
    assert _classify_failure("research", None) is FailureKind.MODEL_ERROR


def test_classify_unknown_error_is_unrecoverable():
    assert _classify_failure("operator", "totally_unknown_error") is FailureKind.UNRECOVERABLE


# ---------- plan_retry (Phase 11 contract preserved in 13a) ----------

def test_plan_retry_returns_fallback_for_persona_llm_error():
    agent = RepairAgent()
    plan = agent.plan_retry("architect", _failed_result("persona_llm_error"), _input())
    assert plan is not None
    assert plan.get("model")


def test_plan_retry_returns_fallback_for_coding_llm_error():
    agent = RepairAgent()
    plan = agent.plan_retry("coding", _failed_result("coding_llm_error"), _input())
    assert plan is not None
    assert plan["model"]


def test_plan_retry_for_evaluator_and_critic_llm_errors():
    agent = RepairAgent()
    assert agent.plan_retry("evaluator", _failed_result("evaluator_llm_error"), _input()) is not None
    assert agent.plan_retry("critic", _failed_result("critic_llm_error"), _input()) is not None


def test_plan_retry_casual_uses_casual_fallback_model():
    agent = RepairAgent()
    plan = agent.plan_retry("casual", _failed_result("persona_llm_error"), _input())
    assert plan is not None
    # casual defaults to models.casual (haiku) per RepairAgent.__init__
    assert plan["model"]


def test_plan_retry_returns_none_for_memory():
    # Memory writes are never retried at the runner level — kind=UNRECOVERABLE.
    agent = RepairAgent()
    assert agent.plan_retry("memory", _failed_result("write_failed"), _input()) is None


def test_plan_retry_returns_none_for_execution_refused():
    # Sandbox refusal is by-design; kind=UNRECOVERABLE.
    agent = RepairAgent()
    assert agent.plan_retry("execution", _failed_result("execution_refused"), _input()) is None


def test_plan_retry_returns_none_for_unknown_error():
    agent = RepairAgent()
    assert agent.plan_retry("research", _failed_result("totally_new_error"), _input()) is None


def test_plan_retry_returns_none_for_parse_error():
    # PARSE_ERROR's plan_recovery ladder leads with same_model_repair_prompt
    # (Phase 13b), which doesn't fit plan_retry's "one model swap" contract.
    # Phase 11's runner saw None here and skipped; Phase 13b changes the
    # runner over to plan_recovery proper.
    agent = RepairAgent()
    assert agent.plan_retry("evaluator", _failed_result("evaluator_parse_error"), _input()) is None


def test_plan_retry_returns_none_for_precondition_missing():
    # PRECONDITION_MISSING goes to peer replacement under plan_recovery (13c).
    # plan_retry's one-model-swap contract can't express that; returns None.
    agent = RepairAgent()
    assert agent.plan_retry("critic", _failed_result("critic_no_candidate"), _input()) is None


def test_plan_retry_returns_none_when_no_fallback_model_configured():
    agent = RepairAgent()
    # Unregistered agent has no fallback entry; MODEL_ERROR kind still returns None.
    assert agent.plan_retry("unknown_agent", _failed_result("persona_llm_error"), _input()) is None


# ---------- RepairAgent shape ----------

def test_default_repair_agent_is_a_singleton():
    assert default_repair_agent.name == "repair"
    assert default_repair_agent.composer is False


def test_run_is_a_no_op_returning_ok():
    agent = RepairAgent()
    result = agent.run(_input(), context=None)
    assert result.ok is True
    assert result.text == ""
