"""The Telegram bot adapter — the one module that imports httpx and speaks the
Bot API.

Loaded lazily by the `ubongo telegram` entrypoint, so a core install without the
optional `[telegram]` extra never imports it. A thin long-poll loop — no heavy
bot framework: `getUpdates` (long poll) -> `service.handle_message` ->
`sendMessage`. The token comes from `TELEGRAM_BOT_TOKEN` in `.env`; it is never
logged and never read from config. All decision logic lives in `service.py`.
"""

from __future__ import annotations

import logging
import os
import time

from ubongo.telegram import service

logger = logging.getLogger("ubongo.telegram.bot")

_API = "https://api.telegram.org/bot{token}/{method}"
_POLL_TIMEOUT = 30  # seconds; long-poll holds the connection open
_BACKOFF = 5        # seconds to wait after a transient API/network error


def run() -> int:
    """Start the long-poll loop. Returns non-zero only on a fatal misconfig
    (missing token); transient API errors are swallowed and retried so the bot
    stays up. `service.bootstrap`-equivalent config/logging is done here."""
    try:
        import httpx
    except ImportError:
        print("The Telegram dependency is not installed.")
        print("Install it with:  ./install.sh --telegram   (or: uv sync --extra telegram)")
        return 1

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Error: TELEGRAM_BOT_TOKEN not set in .env.")
        return 1

    channel_bootstrap()
    allowed = service.allowed_user_ids()
    if not allowed:
        logger.warning("telegram_no_allowed_users")  # deny-all; bot runs but refuses everyone
    logger.info("telegram_bot_started", extra={"allowed_count": len(allowed)})

    offset = 0
    with httpx.Client(timeout=_POLL_TIMEOUT + 10) as client:
        while True:
            try:
                updates = _get_updates(client, token, offset)
            except Exception as exc:  # transient network/API error — log + back off
                logger.warning("telegram_poll_failed", extra={"error": str(exc)[:160]})
                time.sleep(_BACKOFF)
                continue
            for update in updates:
                offset = max(offset, update.get("update_id", 0) + 1)
                _handle_one(client, token, update)


def channel_bootstrap() -> None:
    from ubongo import channel
    channel.bootstrap("telegram")


def _get_updates(client, token: str, offset: int) -> list[dict]:
    resp = client.get(
        _API.format(token=token, method="getUpdates"),
        params={"offset": offset, "timeout": _POLL_TIMEOUT},
    )
    resp.raise_for_status()
    return resp.json().get("result", []) or []


def _handle_one(client, token: str, update: dict) -> None:
    message = update.get("message") or update.get("edited_message")
    if not message:
        return
    chat_id = (message.get("chat") or {}).get("id")
    user_id = (message.get("from") or {}).get("id")
    text = message.get("text", "")
    if chat_id is None or user_id is None:
        return
    try:
        reply = service.handle_message(text, user_id)
    except Exception:
        logger.warning("telegram_handle_failed", exc_info=True)
        reply = "Something went wrong handling that. Check the logs."
    # before_send policy seam (default-allow); a paused channel drops delivery.
    if not service.delivery_allowed(user_id):
        logger.info("telegram_delivery_suppressed", extra={"user_id": user_id})
        return
    _send_message(client, token, chat_id, reply)


def _send_message(client, token: str, chat_id: int, text: str) -> None:
    try:
        client.post(
            _API.format(token=token, method="sendMessage"),
            json={"chat_id": chat_id, "text": text},
        ).raise_for_status()
    except Exception as exc:
        logger.warning("telegram_send_failed", extra={"error": str(exc)[:160]})
