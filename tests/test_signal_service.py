"""The Signal channel's service core (v0.7 phase 00) — auth and turn handling,
all transport-free. The signal-cli client (client.py) is the only module touching
the socket and is exercised with the transport mocked (see test_signal_client.py).

Phase 00 scope: a normal turn round-trips; a gated turn surfaces its decision id
and points at the cross-channel approval surface. The `/approve|/decline|/pending|
/grants` command router over Signal is Phase 01, so there are no command tests here.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.signal import service  # noqa: E402
from ubongo.memory import store  # noqa: E402

import pytest  # noqa: E402


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


def _allow(*numbers):
    """Patch the config so the given E.164 numbers are authorized."""
    cfg = {"signal": {"allowed_numbers": list(numbers)}}
    return patch("ubongo.signal.service.load_config", return_value=cfg)


# --- auth (fail-closed) ---

def test_empty_allowlist_denies_everyone():
    with patch("ubongo.signal.service.load_config",
               return_value={"signal": {"allowed_numbers": []}}):
        assert service.is_allowed("+15551234567") is False


def test_listed_number_is_allowed_unlisted_is_not():
    with _allow("+15551234567", "+15559999999"):
        assert service.is_allowed("+15551234567") is True
        assert service.is_allowed("+15550000000") is False


def test_number_is_whitespace_normalized():
    with _allow("+15551234567"):
        assert service.is_allowed("  +15551234567 ") is True


def test_missing_signal_config_denies():
    with patch("ubongo.signal.service.load_config", return_value={}):
        assert service.is_allowed("+15551234567") is False


# --- unauthorized: refusal, no turn runs ---

def test_unauthorized_sender_gets_refusal_and_no_turn():
    with patch("ubongo.signal.service.load_config",
               return_value={"signal": {"allowed_numbers": []}}), \
         patch("ubongo.signal.service.channel.run_turn") as run:
        out = service.handle_message("hello", "+15550000000")
    assert out == "Not authorized."
    run.assert_not_called()


# --- normal turn (no bypass) ---

def test_authorized_normal_turn_runs_through_the_core():
    with _allow("+15551234567"), \
         patch("ubongo.signal.service.channel.run_turn",
               return_value=(_FakeResponse("the answer"), None)) as run:
        out = service.handle_message("what is a WAL?", "+15551234567")
    assert out == "the answer"
    args, kwargs = run.call_args
    assert args[0] == "what is a WAL?"
    assert kwargs.get("auto_mode") is True


def test_empty_message_prompts_without_a_turn():
    with _allow("+15551234567"), \
         patch("ubongo.signal.service.channel.run_turn") as run:
        out = service.handle_message("   ", "+15551234567")
    assert "start a turn" in out.lower()
    run.assert_not_called()


# --- gated turn: surface the decision id + cross-channel pointer (P00) ---

def test_gated_turn_surfaces_decision_id_and_pointer():
    from ubongo.governance.approval import ApprovalRequest
    appr = ApprovalRequest(decision_id=7, summary="flagged", why="why")
    with _allow("+15551234567"), \
         patch("ubongo.signal.service.channel.run_turn",
               return_value=(_FakeResponse("gated msg", approval=appr), None)):
        out = service.handle_message("delete the entire vault", "+15551234567")
    assert "gated msg" in out
    assert "#7" in out
    assert "ubongo approve 7" in out and "ubongo decline 7" in out


# --- before_send policy seam (minimal, default-allow) ---

def test_delivery_allowed_default_true():
    with patch("ubongo.signal.service.load_config", return_value={"signal": {}}):
        assert service.delivery_allowed("+15551234567") is True


def test_delivery_paused_suppresses():
    with patch("ubongo.signal.service.load_config",
               return_value={"signal": {"delivery_paused": True}}):
        assert service.delivery_allowed("+15551234567") is False
