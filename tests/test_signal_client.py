"""The signal-cli client's transport logic (v0.7 phase 00) — the pure pieces:
parsing a `receive` notification and framing a `send` request. The socket pump
itself needs a live daemon, so it is not exercised here; these cover the byte-level
logic that turns daemon JSON into a (source, text) and a reply back into JSON-RPC.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.signal import client  # noqa: E402


def _receive(source="+15551234567", message="hello", account="+15550000000",
             use_source_number=True):
    envelope = {"dataMessage": {"message": message, "timestamp": 1}}
    if use_source_number:
        envelope["sourceNumber"] = source
    else:
        envelope["source"] = source
    return {"envelope": envelope, "account": account}


# --- _parse_incoming ---

def test_parse_pulls_source_and_text():
    assert client._parse_incoming(_receive(), account="+15550000000") == ("+15551234567", "hello")


def test_parse_falls_back_to_source_when_no_source_number():
    params = _receive(use_source_number=False)
    assert client._parse_incoming(params, account="+15550000000") == ("+15551234567", "hello")


def test_parse_ignores_message_without_text():
    params = {"envelope": {"sourceNumber": "+15551234567", "receiptMessage": {}}}
    assert client._parse_incoming(params, account="+15550000000") is None


def test_parse_ignores_empty_datamessage():
    params = {"envelope": {"sourceNumber": "+15551234567", "dataMessage": {"message": None}}}
    assert client._parse_incoming(params, account="+15550000000") is None


def test_parse_ignores_own_account_to_avoid_loops():
    params = _receive(source="+15550000000")  # source == account
    assert client._parse_incoming(params, account="+15550000000") is None


def test_parse_handles_missing_envelope():
    assert client._parse_incoming({}, account="+15550000000") is None


# --- _send framing ---

class _FakeSock:
    def __init__(self):
        self.sent = b""

    def sendall(self, data):
        self.sent += data


def test_send_frames_jsonrpc_request():
    sock = _FakeSock()
    client._send(sock, "+15550000000", "+15551234567", "the reply", req_id=3)
    line = sock.sent.decode("utf-8")
    assert line.endswith("\n")
    payload = json.loads(line)
    assert payload["jsonrpc"] == "2.0"
    assert payload["method"] == "send"
    assert payload["id"] == 3
    assert payload["params"]["recipient"] == ["+15551234567"]
    assert payload["params"]["message"] == "the reply"
    assert payload["params"]["account"] == "+15550000000"


def test_send_omits_account_when_empty():
    sock = _FakeSock()
    client._send(sock, "", "+15551234567", "hi", req_id=1)
    payload = json.loads(sock.sent.decode("utf-8"))
    assert "account" not in payload["params"]


def test_send_swallows_socket_error():
    class _BadSock:
        def sendall(self, data):
            raise OSError("broken pipe")
    # must not raise — transient send errors are logged, not fatal
    client._send(_BadSock(), "+15550000000", "+15551234567", "hi", req_id=1)
