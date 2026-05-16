from __future__ import annotations

from ubongo.governance.risk import RiskLevel, from_classifier, score_risk

_KEYWORDS = ["rm -rf", "delete the entire", "wipe", "drop table"]


def _classification(risk: str):
    return type("C", (), {"risk": risk})()


def test_from_classifier_maps_known_values():
    assert from_classifier("low") is RiskLevel.LOW
    assert from_classifier("destructive") is RiskLevel.DESTRUCTIVE


def test_from_classifier_unknown_or_none_is_low():
    assert from_classifier(None) is RiskLevel.LOW
    assert from_classifier("bogus") is RiskLevel.LOW


def test_score_risk_uses_classifier_when_no_keyword():
    assert score_risk(_classification("high"), "explain consistent hashing", _KEYWORDS) is RiskLevel.HIGH
    assert score_risk(_classification("low"), "hello there", _KEYWORDS) is RiskLevel.LOW


def test_score_risk_keyword_escalates_to_destructive():
    # Classifier said low, but the message obviously is not.
    assert score_risk(_classification("low"), "please wipe my memory", _KEYWORDS) is RiskLevel.DESTRUCTIVE
    assert score_risk(_classification("medium"), "rm -rf /tmp/x", _KEYWORDS) is RiskLevel.DESTRUCTIVE


def test_score_risk_keyword_match_is_case_insensitive():
    assert score_risk(_classification("low"), "DELETE THE ENTIRE vault", _KEYWORDS) is RiskLevel.DESTRUCTIVE


def test_score_risk_no_keywords_configured_falls_back_to_classifier():
    assert score_risk(_classification("medium"), "wipe everything", []) is RiskLevel.MEDIUM


def test_score_risk_handles_empty_message():
    assert score_risk(_classification("low"), "", _KEYWORDS) is RiskLevel.LOW
