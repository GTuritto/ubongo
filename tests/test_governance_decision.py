from __future__ import annotations

from ubongo.governance.decision import Action, Decision, decide


def _classification(**overrides):
    base = {
        "intent": "technical",
        "tone": "neutral",
        "task_type": "question",
        "suggested_skill": None,
        "risk": "low",
        "confidence": 0.9,
    }
    base.update(overrides)
    return type("Classification", (), base)()


def _workflow(**overrides):
    base = {
        "persona": "architect",
        "model": "openrouter/anthropic/claude-sonnet-4.5",
        "skill_name": None,
        "execution_mode": "sequential",
        "agents": ("architect",),
    }
    base.update(overrides)
    return type("Workflow", (), base)()


def test_decide_returns_auto_for_low_risk_technical():
    d = decide(_classification(), _workflow())
    assert d.action == Action.AUTO.value
    assert d.action == "auto"


def test_decide_returns_auto_for_high_risk_destructive_stub_does_not_gate():
    d = decide(_classification(risk="destructive", intent="work"), _workflow())
    assert d.action == "auto"


def test_decide_returns_auto_when_evaluator_confidence_absent():
    d = decide(_classification(), _workflow(), evaluator_confidence=None)
    assert d.action == "auto"
    assert d.reason is None


def test_decide_returns_auto_when_evaluator_confidence_high():
    d = decide(_classification(), _workflow(), evaluator_confidence=0.85)
    assert d.action == "auto"


def test_decide_rejects_when_evaluator_confidence_below_floor():
    d = decide(_classification(), _workflow(), evaluator_confidence=0.1)
    assert d.action == "reject"
    assert d.reason is not None
    assert "below_floor" in d.reason
    assert "0.10" in d.reason


def test_decide_floor_is_inclusive_at_threshold():
    # 0.2 is the floor itself: at-floor stays auto; just below rejects.
    assert decide(_classification(), _workflow(), evaluator_confidence=0.2).action == "auto"
    assert decide(_classification(), _workflow(), evaluator_confidence=0.199).action == "reject"


def test_action_enum_values_match_schema_vocabulary():
    expected = {"auto", "ask_clarification", "require_approval", "reject"}
    assert {a.value for a in Action} == expected


def test_decision_is_frozen_dataclass():
    d = Decision(action="auto", reason="because")
    try:
        d.action = "reject"  # type: ignore[misc]
    except Exception:
        pass
    else:
        raise AssertionError("Decision should be frozen")
