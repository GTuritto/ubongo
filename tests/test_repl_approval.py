from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ubongo import repl
from ubongo.memory import store
from ubongo.memory import trace

from ubongo.governance.approval import ApprovalRequest

_REQUEST = ApprovalRequest(
    decision_id=7,
    summary="Governance flagged this turn for approval (risk=destructive, reason=risk_destructive).",
    why="Governance held this turn because the request looks destructive.",
)


# --- _prompt_approval ---


def test_prompt_approval_y_returns_y():
    with patch("builtins.input", return_value="y"):
        assert repl._prompt_approval(_REQUEST) == "y"


def test_prompt_approval_n_returns_n():
    with patch("builtins.input", return_value="n"):
        assert repl._prompt_approval(_REQUEST) == "n"


def test_prompt_approval_anything_else_is_n():
    with patch("builtins.input", return_value="maybe"):
        assert repl._prompt_approval(_REQUEST) == "n"


def test_prompt_approval_eof_returns_n():
    with patch("builtins.input", side_effect=EOFError):
        assert repl._prompt_approval(_REQUEST) == "n"


def test_prompt_approval_why_prints_explanation_then_reprompts(capsys):
    # First input is "why" (prints the explanation, re-prompts), then "y".
    with patch("builtins.input", side_effect=["why", "y"]):
        result = repl._prompt_approval(_REQUEST)
    assert result == "y"
    out = capsys.readouterr().out
    assert _REQUEST.why in out
    assert _REQUEST.summary in out


def test_prompt_approval_why_is_case_insensitive(capsys):
    with patch("builtins.input", side_effect=["WHY", "n"]):
        assert repl._prompt_approval(_REQUEST) == "n"
    assert _REQUEST.why in capsys.readouterr().out


# --- trace.update_governance_decision ---


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")
    store.bootstrap()
    yield
    store.set_db_path(None)


def _seed_governance_row() -> int:
    cid = store.start_conversation("casual")
    msg_id = store.append_message(cid, "user", "x", persona="casual")
    wf_id = trace.append_workflow_run(
        conversation_id=cid, message_id=msg_id,
        classification={"intent": "other"}, workflow={"persona": "casual"},
        execution_mode="sequential", outcome="success", started_at=store.now_iso(),
    )
    return trace.append_governance_decision(
        workflow_run_id=wf_id, intent="other", risk="destructive",
        confidence=1.0, reversibility="reversible", action="require_approval",
    )


def test_update_governance_decision_persists_approval_response():
    decision_id = _seed_governance_row()
    before = store.connection().execute(
        "SELECT approval_response FROM governance_decisions WHERE id = ?", (decision_id,)
    ).fetchone()[0]
    assert before is None

    trace.update_governance_decision(decision_id, "y")
    after = store.connection().execute(
        "SELECT approval_response FROM governance_decisions WHERE id = ?", (decision_id,)
    ).fetchone()[0]
    assert after == "y"


def test_update_governance_decision_can_record_decline():
    decision_id = _seed_governance_row()
    trace.update_governance_decision(decision_id, "n")
    row = store.connection().execute(
        "SELECT approval_response FROM governance_decisions WHERE id = ?", (decision_id,)
    ).fetchone()
    assert row[0] == "n"


# --- /pending command (v0.5 phase 03) ---


def _seed_pending(message="delete the entire vault", persona="casual") -> int:
    decision_id = _seed_governance_row()
    trace.append_pending_approval(
        decision_id, message=message, persona=persona, auto_mode=False,
        summary="Governance flagged this turn (risk=destructive).",
        why="held because the request looks destructive",
    )
    return decision_id


def test_pending_lists_open_approvals():
    dec = _seed_pending()
    out = repl._cmd_pending("/pending", repl.ReplState(persona="casual", auto_mode=False, pending_skill=None, pending_workflow=None))
    assert f"#{dec}" in out
    assert "delete the entire vault" in out


def test_pending_empty():
    out = repl._cmd_pending("/pending", repl.ReplState(persona="casual", auto_mode=False, pending_skill=None, pending_workflow=None))
    assert "No pending approvals." in out


def test_pending_approve_resolves_via_seam():
    dec = _seed_pending()
    state = repl.ReplState(persona="casual", auto_mode=False, pending_skill=None, pending_workflow=None)
    with patch("ubongo.master.resume_approval") as resume:
        resume.return_value = type("R", (), {"text": "delivered answer", "persona": "casual"})()
        out = repl._cmd_pending(f"/pending approve {dec}", state)
    resume.assert_called_once_with(dec, "y")
    assert "Approved" in out and "delivered answer" in out


def test_pending_decline_resolves_via_seam():
    dec = _seed_pending()
    state = repl.ReplState(persona="casual", auto_mode=False, pending_skill=None, pending_workflow=None)
    with patch("ubongo.master.resume_approval", return_value=None) as resume:
        out = repl._cmd_pending(f"/pending decline {dec}", state)
    resume.assert_called_once_with(dec, "n")
    assert "Declined" in out


def test_pending_approve_unknown_id():
    out = repl._cmd_pending("/pending approve 9999",
                            repl.ReplState(persona="casual", auto_mode=False, pending_skill=None, pending_workflow=None))
    assert "No pending approval #9999" in out


def test_pending_in_help_banner():
    assert "/pending" in repl._HELP_COMMANDS
