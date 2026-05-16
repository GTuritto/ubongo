from __future__ import annotations

import os

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import router  # noqa: E402
from ubongo.classifier import Classification  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_routing():
    router.reload()
    yield
    router.reload()


def _cls(**overrides) -> Classification:
    base = {
        "intent": "other",
        "tone": "neutral",
        "task_type": "none",
        "suggested_skill": None,
        "risk": "low",
        "confidence": 0.9,
    }
    base.update(overrides)
    return Classification(**base)


def test_technical_intent_routes_to_architect() -> None:
    assert router.route(_cls(intent="technical")) == "architect"


def test_casual_intent_routes_to_casual() -> None:
    assert router.route(_cls(intent="casual")) == "casual"


def test_work_command_routes_to_operator() -> None:
    assert router.route(_cls(intent="work", task_type="command")) == "operator"


def test_work_without_command_falls_through_to_default() -> None:
    # work + non-command doesn't match any rule -> default_workflow (casual_reply -> casual)
    assert router.route(_cls(intent="work", task_type="question")) == "casual"


def test_frustrated_tone_routes_to_supportive_casual() -> None:
    assert router.route(_cls(intent="other", tone="frustrated")) == "casual"


def test_research_routes_to_architect() -> None:
    assert router.route(_cls(intent="research")) == "architect"


def test_coding_routes_to_architect() -> None:
    assert router.route(_cls(intent="coding")) == "architect"


def test_high_stakes_decision_routes_to_architect() -> None:
    assert router.route(_cls(task_type="high_stakes_decision")) == "architect"


def test_unmatched_classification_uses_default_persona() -> None:
    assert router.route(_cls(intent="other", tone="neutral", task_type="none")) == "casual"


def test_first_rule_wins() -> None:
    # technical comes before casual in routing.yaml; even if both could match
    # via different fields, the first matching rule decides.
    assert router.route(_cls(intent="technical", tone="frustrated")) == "architect"


# --- hysteresis ---

def test_hysteresis_keeps_persona_when_suggestion_matches() -> None:
    assert router.apply_hysteresis("architect", "architect", 0.99) == "architect"


def test_hysteresis_keeps_persona_when_confidence_below_threshold() -> None:
    assert router.apply_hysteresis("architect", "casual", 0.5) == "architect"


def test_hysteresis_switches_when_confidence_at_or_above_threshold() -> None:
    assert router.apply_hysteresis("architect", "casual", 0.7) == "casual"
    assert router.apply_hysteresis("casual", "architect", 0.95) == "architect"


def test_hysteresis_threshold_read_from_governance() -> None:
    # governance.yaml has thresholds.auto_route_min_confidence: 0.7
    # 0.69 should not flip; 0.70 should.
    assert router.apply_hysteresis("architect", "casual", 0.69) == "architect"
    assert router.apply_hysteresis("architect", "casual", 0.70) == "casual"


# --- workflows.yaml helpers (Phase 10) ---


def test_workflow_agents_returns_bare_persona_names() -> None:
    assert router.workflow_agents("technical_deep") == ("architect",)
    assert router.workflow_agents("research_brief") == ("research", "architect")


def test_workflow_persona_extracts_from_bare_name_set() -> None:
    assert router.workflow_persona("technical_deep") == "architect"
    assert router.workflow_persona("casual_reply") == "casual"
    assert router.workflow_persona("research_brief") == "architect"


def test_workflow_evaluate_true_for_technical_workflows() -> None:
    assert router.workflow_evaluate("technical_deep") is True
    assert router.workflow_evaluate("research_brief") is True
    assert router.workflow_evaluate("coding_session") is True


def test_workflow_evaluate_false_for_casual_and_quick() -> None:
    assert router.workflow_evaluate("casual_reply") is False
    assert router.workflow_evaluate("supportive_reply") is False
    assert router.workflow_evaluate("quick_action") is False
    assert router.workflow_evaluate("speculative_brief") is False


def test_workflow_evaluate_unknown_workflow_returns_false() -> None:
    assert router.workflow_evaluate("totally-made-up") is False


# --- Phase 11f: coding_session + execution_session ---


def test_coding_session_uses_coding_then_architect() -> None:
    assert router.workflow_agents("coding_session") == ("coding", "architect")
    assert router.workflow_evaluate("coding_session") is True


def test_execution_session_declared_but_not_auto_routed() -> None:
    """The workflow exists in workflows.yaml for /mode-style debug + tests,
    but no rule in routing.yaml maps an intent/tone to it."""
    assert router.workflow_agents("execution_session") == ("execution", "architect")
    assert router.workflow_evaluate("execution_session") is False
    # No routing.yaml rule should map any classification to execution_session.
    from ubongo.classifier import Classification
    for intent in ("technical", "casual", "work", "research", "coding", "other"):
        cls = Classification(
            intent=intent, tone="neutral", task_type="question",
            suggested_skill=None, risk="low", confidence=0.9,
        )
        assert router.route_workflow(cls) != "execution_session"


# --- Phase 12f: mode helpers + new workflows ---


def test_workflow_mode_for_each_new_workflow() -> None:
    assert router.workflow_mode("research_brief_parallel") == "parallel"
    assert router.workflow_mode("coding_competitive") == "competitive"
    assert router.workflow_mode("brief_collaborative") == "collaborative"
    assert router.workflow_mode("debate_then_synthesize") == "debate"
    assert router.workflow_mode("speculative_brief") == "speculative"


def test_workflow_agents_for_each_new_workflow() -> None:
    assert router.workflow_agents("research_brief_parallel") == ("research", "architect")
    assert router.workflow_agents("coding_competitive") == ("coding", "architect", "evaluator")
    assert router.workflow_agents("brief_collaborative") == ("research", "critic", "architect")
    assert router.workflow_agents("debate_then_synthesize") == ("architect", "operator", "architect")
    assert router.workflow_agents("speculative_brief") == ("casual", "architect", "evaluator")


def test_workflow_rounds_for_debate() -> None:
    assert router.workflow_rounds("debate_then_synthesize") == 2
    assert router.workflow_rounds("technical_deep") is None
    assert router.workflow_rounds("totally-made-up") is None


def test_workflow_timeout_s_for_speculative() -> None:
    assert router.workflow_timeout_s("speculative_brief") == 10
    assert router.workflow_timeout_s("technical_deep") is None


def test_workflow_names_lists_all_declared() -> None:
    names = router.workflow_names()
    assert "research_brief_parallel" in names
    assert "coding_competitive" in names
    assert "brief_collaborative" in names
    assert "debate_then_synthesize" in names
    assert "speculative_brief" in names
    assert "technical_deep" in names
    assert "execution_session" in names


def test_unknown_mode_in_workflows_yaml_falls_back_to_sequential(tmp_path, monkeypatch):
    """Phase 12f: if a workflow declares an unknown mode, router logs a
    warning and falls back to 'sequential'."""
    bad_yaml = tmp_path / "workflows.yaml"
    bad_yaml.write_text(
        "workflows:\n  weird_workflow:\n    agents: [\"casual\"]\n    mode: phantom-mode\n"
        "default_workflow: weird_workflow\n",
        encoding="utf-8",
    )
    import ubongo.router as router_mod
    monkeypatch.setattr(router_mod, "_WORKFLOWS_PATH", bad_yaml)
    router_mod.reload()
    try:
        assert router_mod.workflow_mode("weird_workflow") == "sequential"
    finally:
        router_mod.reload()
