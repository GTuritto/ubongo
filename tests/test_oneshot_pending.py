"""One-shot CLI approval subcommands (v0.5 phase 03): `ubongo pending`,
`ubongo approve|decline <id>` — the CLI half of the cross-channel resume."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import oneshot  # noqa: E402
from ubongo.memory import store, trace  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")
    store.bootstrap()
    yield
    store.set_db_path(None)


def _seed_pending(message="delete the entire vault", persona="casual") -> int:
    cid = store.start_conversation(persona)
    msg_id = store.append_message(cid, "user", message, persona=persona)
    wf_id = trace.append_workflow_run(
        conversation_id=cid, message_id=msg_id,
        classification={"intent": "other"}, workflow={"persona": persona},
        execution_mode="sequential", outcome="success", started_at=store.now_iso(),
    )
    dec = trace.append_governance_decision(
        workflow_run_id=wf_id, intent="other", risk="destructive",
        confidence=1.0, reversibility="reversible", action="require_approval",
    )
    trace.append_pending_approval(
        dec, message=message, persona=persona, auto_mode=False,
        summary="flagged", why="destructive",
    )
    return dec


def test_pending_lists(capsys):
    dec = _seed_pending()
    rc = oneshot.list_pending()
    out = capsys.readouterr().out
    assert rc == 0
    assert f"#{dec}" in out and "delete the entire vault" in out


def test_pending_empty(capsys):
    rc = oneshot.list_pending()
    assert rc == 0
    assert "No pending approvals." in capsys.readouterr().out


def test_approve_delivers(capsys):
    dec = _seed_pending()
    fake = type("R", (), {"text": "delivered answer", "ok": True})()
    with patch("ubongo.master.resume_approval", return_value=fake) as resume:
        rc = oneshot.resolve_pending(dec, approve=True)
    resume.assert_called_once_with(dec, "y")
    assert rc == 0
    assert "delivered answer" in capsys.readouterr().out


def test_decline(capsys):
    dec = _seed_pending()
    with patch("ubongo.master.resume_approval", return_value=None) as resume:
        rc = oneshot.resolve_pending(dec, approve=False)
    resume.assert_called_once_with(dec, "n")
    assert rc == 0
    assert "Declined" in capsys.readouterr().out


def test_approve_unknown_id_exits_nonzero(capsys):
    rc = oneshot.resolve_pending(9999, approve=True)
    assert rc == 1
    assert "No pending approval #9999" in capsys.readouterr().err
