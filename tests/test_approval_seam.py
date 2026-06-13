"""The typed, resumable approval seam (v0.5 phase 03).

Covers the pending_approvals record (trace CRUD), master.resume_approval, the
cross-channel resume that is the phase's exit criterion, and idempotency.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import master  # noqa: E402
from ubongo.classifier import Classification  # noqa: E402
from ubongo.governance import approval as gov_approval  # noqa: E402
from ubongo.llm import CompletionResult  # noqa: E402
from ubongo.memory import store, trace  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")
    store.bootstrap()
    yield
    store.set_db_path(None)


def _classification(risk="low", task_type="question"):
    return Classification(
        intent="other", tone="neutral", task_type=task_type, risk=risk,
        confidence=1.0, suggested_skill=None,
    )


def _completion(text):
    return CompletionResult(text=text, model="m", tokens_in=1, tokens_out=1,
                            latency_ms=1, attempts=1)


def _gate_a_turn(message="delete the entire vault", persona="casual", auto_mode=False):
    """Drive a real require_approval turn; return its Response."""
    with patch("ubongo.master.classifier.classify", return_value=_classification()), \
         patch("ubongo.agents.personas.complete", return_value=_completion("the real answer")):
        return master.handle(message, persona, auto_mode=auto_mode)


# --- trace CRUD ---

def test_pending_record_round_trips():
    dec = _gate_a_turn().approval.decision_id
    rec = trace.get_pending_approval(dec)
    assert rec["status"] == "pending"
    assert rec["message"] == "delete the entire vault"
    assert rec["persona"] == "casual"
    assert rec["auto_mode"] is False


def test_open_pending_lists_only_pending_oldest_first():
    d1 = _gate_a_turn("delete the entire vault, run one").approval.decision_id
    d2 = _gate_a_turn("delete the entire vault, run two").approval.decision_id
    ids = [p.decision_id for p in gov_approval.list_pending()]
    assert ids == [d1, d2]
    trace.resolve_pending_approval(d1, "approved")
    assert [p.decision_id for p in gov_approval.list_pending()] == [d2]


def test_resolve_is_idempotent_at_the_store():
    dec = _gate_a_turn().approval.decision_id
    assert trace.resolve_pending_approval(dec, "approved") is True
    assert trace.resolve_pending_approval(dec, "approved") is False  # already resolved


# --- master.resume_approval ---

def test_resume_approve_delivers_and_flips_both_rows():
    dec = _gate_a_turn().approval.decision_id
    with patch("ubongo.master.classifier.classify", return_value=_classification()), \
         patch("ubongo.agents.personas.complete", return_value=_completion("the real answer")):
        resumed = master.resume_approval(dec, "y")
    assert resumed is not None and resumed.text == "the real answer"
    assert resumed.approval is None  # the re-issue bypasses the gate
    assert gov_approval.get_pending(dec).status == "approved"
    gd = store.connection().execute(
        "SELECT approval_response FROM governance_decisions WHERE id = ?", (dec,)
    ).fetchone()[0]
    assert gd == "y"


def test_resume_decline_does_not_deliver():
    dec = _gate_a_turn().approval.decision_id
    assert master.resume_approval(dec, "n") is None
    assert gov_approval.get_pending(dec).status == "declined"
    gd = store.connection().execute(
        "SELECT approval_response FROM governance_decisions WHERE id = ?", (dec,)
    ).fetchone()[0]
    assert gd == "n"


def test_resume_unknown_id_is_noop():
    assert master.resume_approval(9999, "y") is None


def test_resume_double_approve_is_noop():
    dec = _gate_a_turn().approval.decision_id
    with patch("ubongo.master.classifier.classify", return_value=_classification()), \
         patch("ubongo.agents.personas.complete", return_value=_completion("the real answer")):
        first = master.resume_approval(dec, "y")
        second = master.resume_approval(dec, "y")  # already approved
    assert first is not None
    assert second is None  # no duplicate turn, no second answer


def test_cross_channel_resume_needs_no_original_channel():
    """The exit criterion: gate a turn, drop every reference to how it was
    raised, and resume purely from the persisted decision_id."""
    decision_id = _gate_a_turn("delete the entire vault", persona="operator").approval.decision_id
    # Nothing but the id survives — exactly what one-shot/MCP would hand off.
    with patch("ubongo.master.classifier.classify", return_value=_classification()), \
         patch("ubongo.agents.personas.complete", return_value=_completion("the real answer")):
        resumed = master.resume_approval(decision_id, "y")
    assert resumed is not None and resumed.text == "the real answer"
    # the turn was re-issued with the RECORD's persona, not a channel default
    assert resumed.persona == "operator"


def test_auto_and_normal_turns_write_no_pending_record():
    with patch("ubongo.master.classifier.classify", return_value=_classification()), \
         patch("ubongo.agents.personas.complete", return_value=_completion("ok")):
        resp = master.handle("what is a write-ahead log", "architect")
    assert resp.approval is None
    assert gov_approval.list_pending() == []
