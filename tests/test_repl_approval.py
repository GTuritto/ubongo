from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ubongo import repl
from ubongo.memory import store
from ubongo.memory import trace

_REQUEST = {
    "decision_id": 7,
    "summary": "Governance flagged this turn for approval (risk=destructive, reason=risk_destructive).",
    "why": "Governance held this turn because the request looks destructive.",
}


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
    assert _REQUEST["why"] in out
    assert _REQUEST["summary"] in out


def test_prompt_approval_why_is_case_insensitive(capsys):
    with patch("builtins.input", side_effect=["WHY", "n"]):
        assert repl._prompt_approval(_REQUEST) == "n"
    assert _REQUEST["why"] in capsys.readouterr().out


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
