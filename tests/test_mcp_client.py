from __future__ import annotations

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.mcp import client  # noqa: E402


def _cfg(servers):
    return {"mcp": {"servers": servers}}


# ---------- config parsing (SDK-free) ----------


def test_servers_skips_disabled_and_malformed():
    cfg = _cfg({
        "good": {"transport": "http", "url": "http://x/mcp", "risk": "low", "enabled": True},
        "off": {"transport": "http", "url": "http://y/mcp", "risk": "low", "enabled": False},
        "no_url": {"transport": "http", "risk": "low", "enabled": True},
        "bad_risk": {"transport": "http", "url": "http://z/mcp", "risk": "extreme", "enabled": True},
        "bad_transport": {"transport": "carrier-pigeon", "url": "http://w/mcp", "enabled": True},
    })
    with patch("ubongo.mcp.client.load_config", return_value=cfg):
        got = client.servers()
    assert [s.name for s in got] == ["good"]


def test_servers_stdio_requires_command():
    cfg = _cfg({
        "ok": {"transport": "stdio", "command": "/bin/server", "args": ["-q"],
               "risk": "medium", "enabled": True},
        "missing": {"transport": "stdio", "risk": "medium", "enabled": True},
    })
    with patch("ubongo.mcp.client.load_config", return_value=cfg):
        got = client.servers()
    assert [s.name for s in got] == ["ok"]
    assert got[0].args == ("-q",)


def test_max_enabled_risk():
    cfg = _cfg({
        "a": {"transport": "http", "url": "http://a/mcp", "risk": "low", "enabled": True},
        "b": {"transport": "http", "url": "http://b/mcp", "risk": "high", "enabled": True},
    })
    with patch("ubongo.mcp.client.load_config", return_value=cfg):
        assert client.max_enabled_risk() == "high"
    with patch("ubongo.mcp.client.load_config", return_value=_cfg({})):
        assert client.max_enabled_risk() is None


def test_resolved_env_pulls_from_host_environment(monkeypatch):
    monkeypatch.setenv("HOST_TOKEN", "secret-value")
    server = client.ServerConfig(name="s", transport="stdio", command="/bin/x",
                                 env={"CHILD_TOKEN": "HOST_TOKEN", "GONE": "NOT_SET_VAR"})
    resolved = client._resolved_env(server)
    assert resolved == {"CHILD_TOKEN": "secret-value"}


def test_call_tool_unknown_server_is_an_error_result():
    with patch("ubongo.mcp.client.load_config", return_value=_cfg({})):
        result = client.call_tool("ghost", "anything", {})
    assert result.ok is False and "unknown or disabled" in result.error


# ---------- error shaping (transport patched; the real transport round-trip
# lives in the smoke gate's loop-back, which owns its data/ cleanup) ----------

_ONE = _cfg({"s": {"transport": "http", "url": "http://s/mcp", "risk": "low", "enabled": True}})


def test_call_tool_transport_failure_is_an_error_result():
    async def boom(server, op):
        raise ConnectionError("server unreachable")

    with patch("ubongo.mcp.client.load_config", return_value=_ONE), \
         patch("ubongo.mcp.client._with_session", boom):
        result = client.call_tool("s", "t", {})
    assert result.ok is False and "unreachable" in result.error


def test_tool_catalog_skips_unreachable_servers():
    async def boom(server, op):
        raise TimeoutError("no answer")

    with patch("ubongo.mcp.client.load_config", return_value=_ONE), \
         patch("ubongo.mcp.client._with_session", boom):
        assert client.tool_catalog() == []


def test_call_tool_iserror_result_shapes_as_failure():
    class _Content:
        text = "tool blew up"

    class _Result:
        isError = True
        content = [_Content()]

    async def ok(server, op):
        return _Result()

    with patch("ubongo.mcp.client.load_config", return_value=_ONE), \
         patch("ubongo.mcp.client._with_session", ok):
        result = client.call_tool("s", "t", {})
    assert result.ok is False and result.text == "tool blew up"
