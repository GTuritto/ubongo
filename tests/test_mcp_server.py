from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

pytest.importorskip("mcp", reason="optional extra: uv sync --extra mcp")

from mcp.shared.memory import (  # noqa: E402
    create_connected_server_and_client_session as client_session,
)

from ubongo import context, events, skills  # noqa: E402
from ubongo.mcp import server  # noqa: E402
from ubongo.memory import store, vault  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")
    vault.set_vault_root(tmp_path / "vault")
    skills.set_skills_dir(None)
    skills.reload()
    context.reload()
    events.clear()
    yield
    events.clear()
    skills.set_skills_dir(None)
    skills.reload()
    context.reload()
    store.set_db_path(None)
    vault.set_vault_root(None)


def _roundtrip(coro_fn):
    async def runner():
        app = server.build_server()
        async with client_session(app._mcp_server) as client:
            return await coro_fn(client)

    return asyncio.run(runner())


def test_server_lists_tools_and_resources():
    async def go(client):
        tools = await client.list_tools()
        resources = await client.list_resources()
        return tools, resources

    tools, resources = _roundtrip(go)
    assert {t.name for t in tools.tools} == {"ubongo_send", "ubongo_recall"}
    assert {str(r.uri) for r in resources.resources} == {
        "ubongo://vault/daily/today", "ubongo://audit",
    }


def test_recall_tool_roundtrip_offline():
    conv = store.current_or_new_conversation("architect")
    store.append_message(conv, "user", "mcp roundtrip marker", persona="architect")

    async def go(client):
        return await client.call_tool("ubongo_recall", {"query": "marker"})

    result = _roundtrip(go)
    assert result.isError is False
    text = "".join(c.text for c in result.content if hasattr(c, "text"))
    assert "mcp roundtrip marker" in text


def test_send_tool_roundtrip_with_stubbed_master(monkeypatch):
    """The regression that matters: ubongo_send crosses the event loop into a
    worker thread, because the runner is sync-at-the-boundary via asyncio.run
    and must not nest inside the server's loop."""
    from ubongo.master import Response
    from ubongo.mcp import service

    def handle(message, persona, auto_mode=False, **kwargs):
        # prove we are NOT on the event-loop thread: asyncio.run must work here
        asyncio.run(asyncio.sleep(0))
        return Response(text=f"echo:{message}", ok=True, persona=persona,
                        skill_name=None, delivery_token=None)

    monkeypatch.setattr(service.master, "handle", handle)
    monkeypatch.setattr(service.queue, "flush_delivered", lambda token: None)

    async def go(client):
        return await client.call_tool(
            "ubongo_send", {"message": "ping", "persona": "casual"}
        )

    result = _roundtrip(go)
    assert result.isError is False
    text = "".join(c.text for c in result.content if hasattr(c, "text"))
    assert "echo:ping" in text


def test_resource_roundtrip_offline():
    vault.append_audit_entry("governance", "mcp resource marker")

    async def go(client):
        return await client.read_resource("ubongo://audit")

    result = _roundtrip(go)
    text = "".join(c.text for c in result.contents if hasattr(c, "text"))
    assert "mcp resource marker" in text
