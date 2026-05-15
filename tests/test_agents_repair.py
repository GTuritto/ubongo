from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context, events, skills  # noqa: E402
from ubongo.agents.base import AgentInput, AgentResult  # noqa: E402
from ubongo.agents.repair import (  # noqa: E402
    FailureKind,
    RecoveryPlan,
    RepairAgent,
    Strategy,
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


# ---------- plan_recovery (Phase 13b multi-strategy) ----------

def test_plan_recovery_parse_error_leads_with_variant_prompt():
    agent = RepairAgent()
    plan = agent.plan_recovery(
        failed_agent="evaluator",
        original=_failed_result("evaluator_parse_error"),
        attempts_so_far=(),
    )
    assert plan.strategy is Strategy.RETRY_SAME_MODEL_VARIANT_PROMPT
    assert plan.prompt_hint
    assert "JSON" in plan.prompt_hint


def test_plan_recovery_model_error_leads_with_different_model():
    agent = RepairAgent()
    plan = agent.plan_recovery(
        failed_agent="architect",
        original=_failed_result("persona_llm_error"),
        attempts_so_far=(),
    )
    assert plan.strategy is Strategy.RETRY_DIFFERENT_MODEL_SAME_PROMPT
    assert plan.override_model  # populated from fallback_models


def test_plan_recovery_walks_to_smaller_model_on_second_attempt():
    agent = RepairAgent()
    plan = agent.plan_recovery(
        failed_agent="architect",
        original=_failed_result("persona_llm_error"),
        attempts_so_far=(Strategy.RETRY_DIFFERENT_MODEL_SAME_PROMPT,),
    )
    assert plan.strategy is Strategy.RETRY_SMALLER_MODEL_SHORTER_PROMPT
    assert plan.override_model  # casual model by default
    assert plan.max_tokens_cap == 200
    assert plan.prompt_hint and "concise" in plan.prompt_hint.lower()


def test_plan_recovery_skips_peer_when_none_configured():
    # Evaluator's default peer is null; the strategy is skipped and the
    # ladder advances to ABORT (PARSE_ERROR ladder ends in ABORT after PEER).
    agent = RepairAgent()
    agent._peer_replacements = {**agent._peer_replacements, "evaluator": None}
    plan = agent.plan_recovery(
        failed_agent="evaluator",
        original=_failed_result("evaluator_parse_error"),
        attempts_so_far=(
            Strategy.RETRY_SAME_MODEL_VARIANT_PROMPT,
            Strategy.RETRY_DIFFERENT_MODEL_SAME_PROMPT,
        ),
    )
    assert plan.strategy is Strategy.ABORT


def test_plan_recovery_precondition_missing_when_peer_configured():
    # 13c default: critic → architect. PRECONDITION_MISSING leads with peer.
    agent = RepairAgent()
    plan = agent.plan_recovery(
        failed_agent="critic",
        original=_failed_result("critic_no_candidate"),
        attempts_so_far=(),
    )
    assert plan.strategy is Strategy.REPLACE_WITH_PEER
    assert plan.peer_agent == "architect"


def test_peer_replacements_defaults_from_settings():
    """13c: settings.yaml ships sensible peer defaults for the worker agents.
    Asserts the live load reflects the YAML."""
    agent = RepairAgent()
    # workers → architect; personas rotate; structurally-unique → None.
    assert agent._peer_replacements.get("coding") == "architect"
    assert agent._peer_replacements.get("research") == "architect"
    assert agent._peer_replacements.get("critic") == "architect"
    assert agent._peer_replacements.get("evaluator") is None
    assert agent._peer_replacements.get("memory") is None
    assert agent._peer_replacements.get("execution") is None
    assert agent._peer_replacements.get("architect") == "operator"
    assert agent._peer_replacements.get("operator") == "architect"
    assert agent._peer_replacements.get("casual") == "operator"


def test_plan_recovery_aborts_when_max_attempts_reached():
    agent = RepairAgent()
    agent.max_attempts = 2
    plan = agent.plan_recovery(
        failed_agent="architect",
        original=_failed_result("persona_llm_error"),
        attempts_so_far=(
            Strategy.RETRY_DIFFERENT_MODEL_SAME_PROMPT,
            Strategy.RETRY_SMALLER_MODEL_SHORTER_PROMPT,
        ),
    )
    assert plan.strategy is Strategy.ABORT
    assert plan.reason and "max_attempts" in plan.reason


def test_plan_recovery_unrecoverable_kind_returns_abort():
    agent = RepairAgent()
    plan = agent.plan_recovery(
        failed_agent="execution",
        original=_failed_result("execution_refused"),
        attempts_so_far=(),
    )
    assert plan.strategy is Strategy.ABORT


def test_plan_recovery_precondition_missing_no_peer_aborts():
    # Force the peer config empty to verify the ABORT fallback path.
    agent = RepairAgent()
    agent._peer_replacements = {}
    plan = agent.plan_recovery(
        failed_agent="critic",
        original=_failed_result("critic_no_candidate"),
        attempts_so_far=(),
    )
    # No peer configured → ladder advances past REPLACE_WITH_PEER → ABORT.
    assert plan.strategy is Strategy.ABORT


def test_plan_recovery_parse_error_with_peer_configured_walks_full_ladder():
    agent = RepairAgent()
    agent._peer_replacements = {"evaluator": "research"}
    # 0: variant prompt
    p0 = agent.plan_recovery(
        failed_agent="evaluator",
        original=_failed_result("evaluator_parse_error"),
        attempts_so_far=(),
    )
    assert p0.strategy is Strategy.RETRY_SAME_MODEL_VARIANT_PROMPT
    # 1: different model
    p1 = agent.plan_recovery(
        failed_agent="evaluator",
        original=_failed_result("evaluator_parse_error"),
        attempts_so_far=(Strategy.RETRY_SAME_MODEL_VARIANT_PROMPT,),
    )
    assert p1.strategy is Strategy.RETRY_DIFFERENT_MODEL_SAME_PROMPT
    # 2: peer
    p2 = agent.plan_recovery(
        failed_agent="evaluator",
        original=_failed_result("evaluator_parse_error"),
        attempts_so_far=(
            Strategy.RETRY_SAME_MODEL_VARIANT_PROMPT,
            Strategy.RETRY_DIFFERENT_MODEL_SAME_PROMPT,
        ),
    )
    assert p2.strategy is Strategy.REPLACE_WITH_PEER
    assert p2.peer_agent == "research"


# ---------- RepairAgent shape ----------

def test_default_repair_agent_is_a_singleton():
    assert default_repair_agent.name == "repair"
    assert default_repair_agent.composer is False


def test_run_is_a_no_op_returning_ok():
    agent = RepairAgent()
    result = agent.run(_input(), context=None)
    assert result.ok is True
    assert result.text == ""
