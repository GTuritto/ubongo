from __future__ import annotations

from ubongo.governance.confidence import has_evaluator_signal, score_confidence


def _classification(confidence: float):
    return type("C", (), {"confidence": confidence})()


def _result(evaluator_confidence):
    return type("R", (), {"evaluator_confidence": evaluator_confidence})()


def test_score_confidence_prefers_evaluator():
    assert score_confidence(_classification(0.3), _result(0.85)) == 0.85


def test_score_confidence_falls_back_to_classifier_when_no_evaluator():
    assert score_confidence(_classification(0.42), _result(None)) == 0.42


def test_score_confidence_evaluator_zero_is_still_used():
    # 0.0 is a real evaluator verdict, not "absent".
    assert score_confidence(_classification(0.9), _result(0.0)) == 0.0


def test_score_confidence_bad_classifier_value_is_zero():
    assert score_confidence(_classification("oops"), _result(None)) == 0.0


def test_has_evaluator_signal():
    assert has_evaluator_signal(_result(0.5)) is True
    assert has_evaluator_signal(_result(0.0)) is True
    assert has_evaluator_signal(_result(None)) is False
