"""The MCP client session layer (candidate 20, v0.1.5).

The outbound half of the `mcp/` package: the Connector agent's only door to
external MCP servers (Compendium et al.). Owns server configuration, session
lifecycle, tool discovery, and tool calls — a sync facade over the SDK's async
API (`asyncio.run` per operation; the connector runs in the runner's worker
threads, so no event loop is ever running here).

Posture (ADR-0016): sessions are per-turn, not daemons; config carries no
secrets (an `env:` map names environment variables resolved at connect time);
a malformed server entry is logged and skipped, never crashes a turn; the SDK
import is lazy so a core install without the optional extra never pays for it.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

from ubongo.config import load_config

logger = logging.getLogger("ubongo.mcp.client")

_RISK_LEVELS = ("low", "medium", "high")
_RISK_ORDER = {r: i for i, r in enumerate(_RISK_LEVELS)}
_DEFAULT_TIMEOUT_S = 20.0


@dataclass(frozen=True)
class ServerConfig:
    name: str
    transport: str  # "stdio" | "http"
    risk: str = "medium"
    url: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict = field(default_factory=dict)  # {child VAR: host ENV name}


@dataclass(frozen=True)
class ToolInfo:
    server: str
    name: str
    description: str
    input_schema: dict


@dataclass(frozen=True)
class ToolResult:
    server: str
    tool: str
    ok: bool
    text: str
    error: str | None = None


def sdk_available() -> bool:
    try:
        import mcp  # noqa: F401
        return True
    except ImportError:
        return False


def servers() -> list[ServerConfig]:
    """The enabled, well-formed server entries from settings `mcp.servers`.
    Parse-tolerant: a bad entry logs `mcp_server_config_invalid` and is
    skipped."""
    block = (load_config().get("mcp", {}) or {}).get("servers", {}) or {}
    out: list[ServerConfig] = []
    for name, raw in block.items():
        raw = raw or {}
        if not raw.get("enabled", False):
            continue
        transport = raw.get("transport")
        risk = raw.get("risk", "medium")
        ok = (
            transport in ("stdio", "http")
            and risk in _RISK_LEVELS
            and (raw.get("url") if transport == "http" else raw.get("command"))
        )
        if not ok:
            logger.warning("mcp_server_config_invalid", extra={"server": name})
            continue
        out.append(ServerConfig(
            name=str(name), transport=transport, risk=risk,
            url=raw.get("url"), command=raw.get("command"),
            args=tuple(raw.get("args", []) or []),
            env=dict(raw.get("env", {}) or {}),
        ))
    return out


def max_enabled_risk() -> str | None:
    """The highest declared risk among enabled servers (governance input)."""
    risks = [s.risk for s in servers()]
    return max(risks, key=_RISK_ORDER.get) if risks else None


def _resolved_env(server: ServerConfig) -> dict | None:
    """Map the server's `env:` entries (child VAR -> host ENV name) to values.
    A missing host variable is logged and the entry is dropped — secrets stay
    in the environment / .env, never in config."""
    if not server.env:
        return None
    resolved = {}
    for var, env_name in server.env.items():
        value = os.environ.get(str(env_name))
        if value is None:
            logger.warning("mcp_server_env_missing",
                           extra={"server": server.name, "env": str(env_name)})
            continue
        resolved[str(var)] = value
    return resolved or None


async def _with_session(server: ServerConfig, op):
    """Open transport + session, initialize, run `op(session)`, close."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamablehttp_client

    if server.transport == "stdio":
        params = StdioServerParameters(
            command=server.command, args=list(server.args),
            env=_resolved_env(server),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await op(session)
    else:
        async with streamablehttp_client(server.url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await op(session)


def tool_catalog(timeout_s: float = _DEFAULT_TIMEOUT_S) -> list[ToolInfo]:
    """Discover tools across every enabled server. A server that fails to
    answer is logged and contributes nothing (the connector reports honestly
    on what it could see)."""
    catalog: list[ToolInfo] = []
    for server in servers():
        async def _list(session):
            return await session.list_tools()

        try:
            listed = asyncio.run(
                asyncio.wait_for(_with_session(server, _list), timeout_s)
            )
        except Exception as exc:
            logger.warning("mcp_list_tools_failed",
                           extra={"server": server.name, "cause": str(exc)})
            continue
        for tool in listed.tools:
            catalog.append(ToolInfo(
                server=server.name, name=tool.name,
                description=tool.description or "",
                input_schema=dict(tool.inputSchema or {}),
            ))
    return catalog


def call_tool(server_name: str, tool: str, arguments: dict,
              timeout_s: float = _DEFAULT_TIMEOUT_S) -> ToolResult:
    """Call one tool on one configured server. Never raises: every failure
    shape (unknown server, transport error, timeout, tool isError) comes back
    as ToolResult(ok=False, error=...)."""
    server = next((s for s in servers() if s.name == server_name), None)
    if server is None:
        return ToolResult(server=server_name, tool=tool, ok=False, text="",
                          error=f"unknown or disabled server '{server_name}'")

    async def _call(session):
        return await session.call_tool(tool, arguments or {})

    try:
        result = asyncio.run(
            asyncio.wait_for(_with_session(server, _call), timeout_s)
        )
    except Exception as exc:
        logger.warning("mcp_call_tool_failed",
                       extra={"server": server_name, "tool": tool, "cause": str(exc)})
        return ToolResult(server=server_name, tool=tool, ok=False, text="",
                          error=str(exc))
    text = "".join(c.text for c in result.content if hasattr(c, "text"))
    if result.isError:
        return ToolResult(server=server_name, tool=tool, ok=False,
                          text=text, error=text or "tool returned an error")
    logger.info("mcp_tool_called",
                extra={"server": server_name, "tool": tool, "chars": len(text)})
    return ToolResult(server=server_name, tool=tool, ok=True, text=text)
