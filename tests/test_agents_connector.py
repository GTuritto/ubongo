from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context, events, skills  # noqa: E402
from ubongo.agents.base import AgentDirectives, AgentInput  # noqa: E402
from ubongo.agents.connector import ConnectorAgent, _parse_plan  # noqa: E402
from ubongo.llm import CompletionResult  # noqa: E402
from ubongo.memory import store, vault  # noqa: E402
from ubongo.mcp.client import ToolInfo, ToolResult  # noqa: E402


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


def _input(message="look this up in compendium"):
    return AgentInput(message=message, history=(), summary_text=None,
                      prior_findings=(), directives=AgentDirectives())


def _completion(text):
    return CompletionResult(text=text, model="m", tokens_in=10, tokens_out=5,
                            latency_ms=12, attempts=1)


_CATALOG = [ToolInfo(server="compendium", name="search",
                     description="search the compendium",
                     input_schema={"properties": {"query": {"type": "string"}}})]


# ---------- plan parsing ----------


def test_parse_plan_accepts_bare_and_fenced_json():
    plan = '{"calls": [{"server": "s", "tool": "t", "arguments": {"q": 1}}], "reason": "r"}'
    assert _parse_plan(plan)[0]["tool"] == "t"
    fenced = "```json\n" + plan + "\n```"
    assert _parse_plan(fenced)[0]["server"] == "s"


def test_parse_plan_empty_and_garbage():
    assert _parse_plan('{"calls": []}') == []
    assert _parse_plan("no json here") is None
    assert _parse_plan('{"calls": "nope"}') is None


def test_parse_plan_caps_calls_and_drops_malformed():
    calls = [{"server": "s", "tool": f"t{i}"} for i in range(9)] + [{"bogus": True}]
    plan = {"calls": calls}
    import json
    out = _parse_plan(json.dumps(plan))
    assert len(out) == 4  # _MAX_CALLS_PER_TURN


# ---------- run() shapes ----------


def test_run_without_sdk_is_honest_ok():
    with patch("ubongo.mcp.client.sdk_available", return_value=False):
        result = ConnectorAgent().run(_input(), None)
    assert result.ok is True and "not installed" in result.text


def test_run_without_servers_is_honest_ok():
    with patch("ubongo.mcp.client.sdk_available", return_value=True), \
         patch("ubongo.mcp.client.servers", return_value=[]):
        result = ConnectorAgent().run(_input(), None)
    assert result.ok is True and "No MCP servers" in result.text


def test_run_executes_planned_calls_and_returns_finding():
    plan = '{"calls": [{"server": "compendium", "tool": "search", "arguments": {"query": "x"}}]}'
    with patch("ubongo.mcp.client.sdk_available", return_value=True), \
         patch("ubongo.mcp.client.servers", return_value=[object()]), \
         patch("ubongo.mcp.client.tool_catalog", return_value=_CATALOG), \
         patch("ubongo.mcp.client.call_tool",
               return_value=ToolResult(server="compendium", tool="search",
                                       ok=True, text="found it")) as m_call, \
         patch("ubongo.agents.connector.complete", return_value=_completion(plan)):
        result = ConnectorAgent().run(_input(), None)
    assert result.ok is True
    assert "found it" in result.text
    assert result.metadata["mcp_calls"] == [
        {"server": "compendium", "tool": "search", "ok": True}]
    m_call.assert_called_once_with("compendium", "search", {"query": "x"})
    # the audit row landed
    assert any("[mcp]" in row for row in vault.audit_tail())


def test_run_empty_plan_is_no_tool_finding():
    with patch("ubongo.mcp.client.sdk_available", return_value=True), \
         patch("ubongo.mcp.client.servers", return_value=[object()]), \
         patch("ubongo.mcp.client.tool_catalog", return_value=_CATALOG), \
         patch("ubongo.agents.connector.complete",
               return_value=_completion('{"calls": []}')):
        result = ConnectorAgent().run(_input(), None)
    assert result.ok is True and "No external tool applies" in result.text


def test_run_unparseable_plan_is_repairable_failure():
    with patch("ubongo.mcp.client.sdk_available", return_value=True), \
         patch("ubongo.mcp.client.servers", return_value=[object()]), \
         patch("ubongo.mcp.client.tool_catalog", return_value=_CATALOG), \
         patch("ubongo.agents.connector.complete",
               return_value=_completion("I think the answer is...")):
        result = ConnectorAgent().run(_input(), None)
    assert result.ok is False and result.error == "connector_mcp_error"


def test_run_all_calls_failed_is_repairable_failure():
    plan = '{"calls": [{"server": "compendium", "tool": "search", "arguments": {}}]}'
    with patch("ubongo.mcp.client.sdk_available", return_value=True), \
         patch("ubongo.mcp.client.servers", return_value=[object()]), \
         patch("ubongo.mcp.client.tool_catalog", return_value=_CATALOG), \
         patch("ubongo.mcp.client.call_tool",
               return_value=ToolResult(server="compendium", tool="search",
                                       ok=False, text="", error="down")), \
         patch("ubongo.agents.connector.complete", return_value=_completion(plan)):
        result = ConnectorAgent().run(_input(), None)
    assert result.ok is False and result.error == "connector_mcp_error"
    assert "FAILED" in result.text
