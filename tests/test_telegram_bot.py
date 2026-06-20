"""The Telegram bot loop (v0.5 phase 04) — the update→reply pump, with the
transport (httpx) mocked. The Bot API is never actually called."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.telegram import bot  # noqa: E402


def test_handle_one_routes_to_service_and_sends():
    client = MagicMock()
    update = {"update_id": 5, "message": {"chat": {"id": 42}, "from": {"id": 7}, "text": "hi"}}
    with patch("ubongo.telegram.service.handle_message", return_value="the reply") as h, \
         patch("ubongo.telegram.service.delivery_allowed", return_value=True), \
         patch("ubongo.telegram.bot._send_message") as send:
        bot._handle_one(client, "tok", update)
    h.assert_called_once_with("hi", 7)
    send.assert_called_once()
    assert send.call_args[0][3] == "the reply"  # (client, token, chat_id, text)


def test_handle_one_suppressed_when_policy_denies():
    client = MagicMock()
    update = {"update_id": 5, "message": {"chat": {"id": 42}, "from": {"id": 7}, "text": "hi"}}
    with patch("ubongo.telegram.service.handle_message", return_value="reply"), \
         patch("ubongo.telegram.service.delivery_allowed", return_value=False), \
         patch("ubongo.telegram.bot._send_message") as send:
        bot._handle_one(client, "tok", update)
    send.assert_not_called()


def test_handle_one_ignores_non_message_updates():
    client = MagicMock()
    with patch("ubongo.telegram.bot._send_message") as send:
        bot._handle_one(client, "tok", {"update_id": 1})  # no message
    send.assert_not_called()


def test_handle_one_survives_a_handler_exception():
    client = MagicMock()
    update = {"update_id": 5, "message": {"chat": {"id": 42}, "from": {"id": 7}, "text": "hi"}}
    with patch("ubongo.telegram.service.handle_message", side_effect=RuntimeError("boom")), \
         patch("ubongo.telegram.service.delivery_allowed", return_value=True), \
         patch("ubongo.telegram.bot._send_message") as send:
        bot._handle_one(client, "tok", update)  # must not raise
    assert "went wrong" in send.call_args[0][3]


def test_run_without_token_exits_nonzero(capsys):
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": ""}, clear=False):
        rc = bot.run()
    assert rc == 1
    assert "TELEGRAM_BOT_TOKEN not set" in capsys.readouterr().out
