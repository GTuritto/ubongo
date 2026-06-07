"""The web channel's turn helper drives the same seam as one-shot."""

from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.web import turn  # noqa: E402


class _FakeToken:
    pass


class _FakeResponse:
    def __init__(self):
        self.text = "hello"
        self.ok = True
        self.delivery_token = _FakeToken()
        self.approval = None


def test_run_turn_calls_master_then_flushes():
    resp = _FakeResponse()
    with patch("ubongo.web.turn.master.handle", return_value=resp) as m_handle, \
         patch("ubongo.web.turn.queue.flush_delivered") as m_flush:
        out = turn.run_turn("hi", "casual", auto_mode=False)
    assert out is resp
    m_handle.assert_called_once_with("hi", "casual", auto_mode=False, approved=False)
    m_flush.assert_called_once_with(resp.delivery_token)


def test_run_turn_forwards_approved_flag():
    resp = _FakeResponse()
    with patch("ubongo.web.turn.master.handle", return_value=resp) as m_handle, \
         patch("ubongo.web.turn.queue.flush_delivered"):
        turn.run_turn("delete the vault", "casual", auto_mode=True, approved=True)
    m_handle.assert_called_once_with(
        "delete the vault", "casual", auto_mode=True, approved=True
    )


def test_bootstrap_is_idempotent():
    fake_cfg = {"logging": {"level": "INFO"}}
    with patch("ubongo.web.turn.load_config", return_value=fake_cfg), \
         patch("ubongo.web.turn.setup_logging") as m_setup:
        turn._bootstrapped = False
        turn.bootstrap()
        turn.bootstrap()
    # setup_logging runs only on the first bootstrap.
    assert m_setup.call_count == 1
