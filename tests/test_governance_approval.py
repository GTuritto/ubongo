from __future__ import annotations

from ubongo.governance.approval import ApprovalRequest, build_request, explain


def _decision(action="require_approval", reason="risk_destructive",
              risk="destructive", reversibility="reversible"):
    return type("D", (), {
        "action": action, "reason": reason,
        "risk": risk, "reversibility": reversibility,
    })()


def test_build_request_carries_decision_id():
    req = build_request(42, _decision(), "delete the entire vault")
    assert isinstance(req, ApprovalRequest)
    assert req.decision_id == 42


def test_summary_names_risk_and_reason():
    req = build_request(1, _decision(risk="destructive", reason="risk_destructive"), "x")
    assert "risk=destructive" in req.summary
    assert "risk_destructive" in req.summary


def test_why_explains_destructive_reason():
    req = build_request(1, _decision(reason="risk_destructive"), "wipe everything")
    assert "destructive" in req.why
    assert "risk=destructive" in req.why
    assert "reversibility=reversible" in req.why
    # Approving / declining semantics are spelled out.
    assert "Approving" in req.why and "declining" in req.why


def test_why_explains_irreversible_high_risk_reason():
    d = _decision(reason="irreversible_high_risk", risk="high", reversibility="irreversible")
    txt = explain(d, "run the migration")
    assert "high-risk" in txt
    assert "cannot be undone" in txt


def test_why_echoes_a_truncated_request():
    long_msg = "delete " + "x" * 300
    txt = explain(_decision(), long_msg)
    assert "..." in txt
    # The echoed snippet stays bounded.
    assert len(txt) < 600


def test_unknown_reason_falls_back_gracefully():
    req = build_request(1, _decision(reason="something_new"), "x")
    assert "governance matrix" in req.why
