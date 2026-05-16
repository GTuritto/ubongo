from __future__ import annotations

from ubongo.governance.decision import Action, Decision, decide

# A self-contained governance config so tests do not depend on governance.yaml.
_GOV = {
    "thresholds": {
        "reject_below_confidence": 0.2,
        "clarification_below_confidence": 0.5,
        "critic_band": [0.2, 0.6],
    },
    "require_approval": {"risks": ["destructive"], "irreversible_high_risk": True},
    "destructive_keywords": ["rm -rf", "delete the entire", "wipe"],
}


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


def _result(evaluator_confidence=None):
    return type("WorkflowResult", (), {"evaluator_confidence": evaluator_confidence})()


def _decide(classification=None, workflow=None, result=None, message="hello"):
    return decide(
        classification or _classification(),
        workflow or _workflow(),
        result or _result(),
        message=message,
        governance=_GOV,
    )


# --- Rule 5: auto ---


def test_low_risk_question_auto_approves():
    d = _decide()
    assert d.action == Action.AUTO.value
    assert d.reason is None
    assert d.risk == "low"
    assert d.reversibility == "reversible"


def test_high_confidence_evaluator_stays_auto():
    d = _decide(result=_result(0.85))
    assert d.action == "auto"
    assert d.confidence == 0.85


# --- Rule 1: destructive risk -> require_approval ---


def test_destructive_classifier_risk_requires_approval():
    d = _decide(_classification(risk="destructive", intent="work"))
    assert d.action == Action.REQUIRE_APPROVAL.value
    assert d.reason == "risk_destructive"


def test_destructive_keyword_backstop_requires_approval():
    # Classifier said low, but the message obviously is destructive.
    d = _decide(_classification(risk="low"), message="please wipe the whole vault")
    assert d.action == "require_approval"
    assert d.risk == "destructive"


def test_destructive_outranks_low_confidence_reject():
    # Safety before quality: a destructive turn gates even with a bad answer.
    d = _decide(_classification(risk="destructive"), result=_result(0.05))
    assert d.action == "require_approval"


# --- Rule 2: high risk + irreversible -> require_approval ---


def test_high_risk_irreversible_requires_approval():
    d = _decide(_classification(risk="high"), workflow=_workflow(agents=("execution", "architect")))
    assert d.action == "require_approval"
    assert d.reason == "irreversible_high_risk"
    assert d.reversibility == "irreversible"


def test_high_risk_reversible_does_not_gate():
    d = _decide(_classification(risk="high"))
    assert d.action == "auto"


# --- Rule 3: low evaluator confidence -> reject ---


def test_low_evaluator_confidence_rejects():
    d = _decide(result=_result(0.1))
    assert d.action == Action.REJECT.value
    assert "below_floor" in d.reason
    assert "0.10" in d.reason


def test_reject_floor_is_inclusive():
    assert _decide(result=_result(0.2)).action == "auto"
    assert _decide(result=_result(0.199)).action == "reject"


def test_no_evaluator_signal_never_rejects():
    # A casual turn with no evaluator: classifier confidence must not reject.
    d = _decide(_classification(confidence=0.05), result=_result(None))
    assert d.action == "auto"


# --- Rule 4: under-specified command -> ask_clarification ---


def test_low_confidence_command_asks_clarification():
    d = _decide(_classification(task_type="command", confidence=0.4))
    assert d.action == Action.ASK_CLARIFICATION.value
    assert "command_low_classifier_confidence" in d.reason


def test_confident_command_auto_approves():
    d = _decide(_classification(task_type="command", confidence=0.9))
    assert d.action == "auto"


def test_low_confidence_non_command_does_not_ask_clarification():
    d = _decide(_classification(task_type="question", confidence=0.3))
    assert d.action == "auto"


# --- enum / dataclass invariants ---


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
