"""The signal-cli adapter — the one module that speaks JSON-RPC to the daemon.

Loaded lazily by the `ubongo signal` entrypoint, so a core install never imports
it. Unlike the Telegram bot (httpx against a cloud API), the Signal transport is a
locally-run **signal-cli daemon** and this client speaks to it over a UNIX socket
using newline-delimited JSON-RPC 2.0 — pure stdlib, no pip dependency. The real
prerequisite is the external signal-cli process (a registered dedicated number);
see `docs/signal-setup.md`.

A thin pump, mirroring `telegram/bot.py`: read `receive` notifications from the
socket -> `service.handle_message` -> write a `send` request back. All decision
logic lives in `service.py`; this module only moves bytes. Transient socket errors
are swallowed and the connection is retried so the channel stays up.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time

from ubongo.config import load_config
from ubongo.signal import service

logger = logging.getLogger("ubongo.signal.client")

_BACKOFF = 5  # seconds to wait after a transient socket error before reconnecting


def _signal_config() -> dict:
    return load_config().get("signal", {}) or {}


def run() -> int:
    """Start the receive->handle->reply pump. Returns non-zero only on a fatal
    misconfig (no socket configured / not reachable); transient socket errors are
    retried so the channel stays up."""
    cfg = _signal_config()
    socket_path = os.path.expanduser(str(cfg.get("socket", "") or "").strip())
    account = str(cfg.get("account", "") or "").strip()

    if not socket_path:
        print("Error: signal.socket is not set in config/settings.yaml.")
        print("Signal needs a running signal-cli daemon (a JSON-RPC UNIX socket).")
        print("See docs/signal-setup.md to register a number and start the daemon.")
        return 1
    if not os.path.exists(socket_path):
        print(f"Error: signal-cli socket not found at {socket_path}.")
        print("Start the signal-cli daemon first (see docs/signal-setup.md):")
        print(f"  signal-cli -a {account or '+<number>'} daemon --socket {socket_path}")
        return 1

    _channel_bootstrap()
    allowed = service.allowed_numbers()
    if not allowed:
        logger.warning("signal_no_allowed_numbers")  # deny-all; runs but refuses everyone
    logger.info("signal_client_started", extra={"allowed_count": len(allowed)})

    while True:
        try:
            _pump(socket_path, account)
        except Exception as exc:  # transient socket/daemon error — log + back off
            logger.warning("signal_pump_failed", extra={"error": str(exc)[:160]})
            time.sleep(_BACKOFF)


def _channel_bootstrap() -> None:
    from ubongo import channel
    channel.bootstrap("signal")


def _pump(socket_path: str, account: str) -> None:
    """One connection's lifetime: read newline-delimited JSON-RPC from the daemon,
    dispatch each `receive` to the handler, write the reply back. Returns (and the
    caller reconnects) when the socket closes."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(socket_path)
        req_id = 0
        with sock.makefile("r", encoding="utf-8") as reader:
            for line in reader:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("method") != "receive":
                    continue  # send responses, typing/receipt notifications, etc.
                parsed = _parse_incoming(msg.get("params") or {}, account)
                if parsed is None:
                    continue
                source_number, text = parsed
                try:
                    reply = service.handle_message(text, source_number)
                except Exception:
                    logger.warning("signal_handle_failed", exc_info=True)
                    reply = "Something went wrong handling that. Check the logs."
                # before_send policy seam (default-allow); a paused channel drops it.
                if not service.delivery_allowed(source_number):
                    logger.info("signal_delivery_suppressed", extra={"source": source_number})
                    continue
                req_id += 1
                _send(sock, account, source_number, reply, req_id)


def _parse_incoming(params: dict, account: str):
    """Pull (source_number, text) out of a signal-cli `receive` notification, or
    None for anything without a text body (receipts, reactions, sync, self)."""
    envelope = params.get("envelope") or {}
    source = envelope.get("sourceNumber") or envelope.get("source")
    data = envelope.get("dataMessage") or {}
    text = data.get("message")
    if not source or not text:
        return None
    if account and str(source).strip() == account:
        return None  # ignore our own account's messages (no loops)
    return str(source).strip(), str(text)


def _send(sock: socket.socket, account: str, recipient: str, message: str, req_id: int) -> None:
    params = {"recipient": [recipient], "message": message}
    if account:
        params["account"] = account
    payload = {"jsonrpc": "2.0", "method": "send", "params": params, "id": req_id}
    try:
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
    except Exception as exc:
        logger.warning("signal_send_failed", extra={"error": str(exc)[:160]})
