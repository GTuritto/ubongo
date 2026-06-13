"""The Telegram channel's service core (v0.5 phase 04) — auth, the command
router, and turn handling, all network-free. The bot loop (bot.py) is the only
module touching httpx/the Bot API and is exercised with the transport mocked."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.telegram import service  # noqa: E402
from ubongo.memory import store, trace, grant_state  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")
    store.bootstrap()
    yield
    store.set_db_path(None)


class _FakeResponse:
    def __init__(self, text="ok", approval=None):
        self.text = text
        self.approval = approval
        self.persona = "architect"


def _allow(*ids):
    """Patch the config so the given user ids are authorized."""
    cfg = {"telegram": {"allowed_user_ids": list(ids)}}
    return patch("ubongo.telegram.service.load_config", return_value=cfg)


# --- auth ---

def test_empty_allowlist_denies_everyone():
    with patch("ubongo.telegram.service.load_config", return_value={"telegram": {"allowed_user_ids": []}}):
        assert service.is_allowed(123) is False


def test_listed_user_is_allowed():
    with _allow(123, 456):
        assert service.is_allowed(123) is True
        assert service.is_allowed(999) is False


def test_unauthorized_user_gets_refusal_and_no_turn():
    with patch("ubongo.telegram.service.load_config", return_value={"telegram": {"allowed_user_ids": []}}), \
         patch("ubongo.telegram.service.channel.run_turn") as run:
        out = service.handle_message("hello", 999)
    assert out == "Not authorized."
    run.assert_not_called()


# --- normal turn (no bypass) ---

def test_authorized_normal_turn_runs_through_the_core():
    with _allow(123), \
         patch("ubongo.telegram.service.channel.run_turn",
               return_value=(_FakeResponse("the answer"), None)) as run:
        out = service.handle_message("what is a WAL?", 123)
    assert out == "the answer"
    # routed through the one seam, auto-routed persona
    args, kwargs = run.call_args
    assert args[0] == "what is a WAL?"
    assert kwargs.get("auto_mode") is True


def test_gated_turn_surfaces_decision_id():
    from ubongo.governance.approval import ApprovalRequest
    appr = ApprovalRequest(decision_id=7, summary="flagged", why="why")
    with _allow(123), \
         patch("ubongo.telegram.service.channel.run_turn",
               return_value=(_FakeResponse("gated msg", approval=appr), None)):
        out = service.handle_message("delete the entire vault", 123)
    assert "gated msg" in out
    assert "/approve 7" in out and "/decline 7" in out


# --- command router (reuses Phase 03/05 seams) ---

def _seed_pending(message="delete the entire vault", persona="casual") -> int:
    cid = store.start_conversation(persona)
    msg_id = store.append_message(cid, "user", message, persona=persona)
    wf = trace.append_workflow_run(conversation_id=cid, message_id=msg_id,
                                   classification={"intent": "other"}, workflow={"persona": persona},
                                   execution_mode="sequential", outcome="success", started_at=store.now_iso())
    dec = trace.append_governance_decision(workflow_run_id=wf, intent="other", risk="destructive",
                                           confidence=1.0, reversibility="reversible", action="require_approval")
    trace.append_pending_approval(dec, message=message, persona=persona, auto_mode=False,
                                  summary="flagged", why="why")
    return dec


def test_pending_command_lists_open_approvals():
    dec = _seed_pending()
    with _allow(123):
        out = service.handle_message("/pending", 123)
    assert f"#{dec}" in out and "delete the entire vault" in out


def test_approve_command_resolves_via_seam():
    dec = _seed_pending()
    with _allow(123), \
         patch("ubongo.telegram.service.master.resume_approval") as resume:
        resume.return_value = _FakeResponse("delivered answer")
        out = service.handle_message(f"/approve {dec}", 123)
    resume.assert_called_once_with(dec, "y")
    assert "Approved" in out and "delivered answer" in out


def test_decline_command():
    dec = _seed_pending()
    with _allow(123), patch("ubongo.telegram.service.master.resume_approval", return_value=None) as resume:
        out = service.handle_message(f"/decline {dec}", 123)
    resume.assert_called_once_with(dec, "n")
    assert "Declined" in out


def test_approve_unknown_id():
    with _allow(123):
        out = service.handle_message("/approve 9999", 123)
    assert "No pending approval #9999" in out


def test_grants_command_lists_active():
    grant_state.grant("connector:compendium")
    with _allow(123):
        out = service.handle_message("/grants", 123)
    assert "connector:compendium" in out


def test_help_on_empty_and_start():
    with _allow(123):
        assert "Commands:" in service.handle_message("/help", 123)
        assert "Commands:" in service.handle_message("", 123)


# --- before_send policy seam ---

def test_delivery_allowed_default_and_paused():
    with patch("ubongo.telegram.service.load_config", return_value={"telegram": {}}):
        assert service.delivery_allowed(123) is True
    with patch("ubongo.telegram.service.load_config", return_value={"telegram": {"delivery_paused": True}}):
        assert service.delivery_allowed(123) is False
