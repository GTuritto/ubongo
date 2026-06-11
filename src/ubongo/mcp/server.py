"""The MCP server adapter — the one module that imports the `mcp` SDK.

Loaded lazily by the `ubongo mcp` entrypoint (and by tests behind
`importorskip`), so a core install without the optional extra never imports
it. All behavior lives in `service.py`; this module only declares the MCP
surface: two tools (`ubongo_send`, `ubongo_recall`) and two read-only
resources (`ubongo://vault/daily/today`, `ubongo://audit`).

Transports: stdio (the client spawns `ubongo mcp`) or streamable HTTP
(`ubongo mcp --http`), bound to the LAN with the same no-auth home-LAN-only
posture as the web UI (ADR-0015, docs/SECURITY.md).
"""

from __future__ import annotations

import functools

import anyio
from mcp.server.fastmcp import FastMCP

from ubongo.mcp import service

_INSTRUCTIONS = (
    "Ubongo is a personal, multi-agent AI mind. ubongo_send runs one full "
    "turn through its orchestration pipeline (classification, agent fleet, "
    "governance, memory) and returns the composed response; a turn the "
    "governance gate holds for approval returns gated=true and cannot be "
    "approved over MCP. ubongo_recall and the resources are read-only views "
    "of its memory."
)


def build_server(host: str = "0.0.0.0", port: int = 8765) -> FastMCP:
    service.bootstrap()
    app = FastMCP("ubongo", instructions=_INSTRUCTIONS, host=host, port=port)

    @app.tool(
        description=(
            "Run one full Ubongo turn: the message goes through the complete "
            "pipeline (classify, plan, execute the agent fleet, govern, "
            "compose, remember). Returns text, ok, persona, gated, "
            "requires_user_decision. gated=true means governance held the "
            "turn for human approval — it cannot be approved over MCP."
        )
    )
    async def ubongo_send(message: str, persona: str = "architect", auto: bool = False) -> service.SendResult:
        # On a worker thread: the runner is sync-at-the-boundary via
        # asyncio.run, which must not nest inside the server's event loop.
        return await anyio.to_thread.run_sync(
            functools.partial(service.send_turn, message, persona, auto)
        )

    @app.tool(
        description=(
            "Read-only recall from Ubongo's memory: the rolling summary, the "
            "recency window, and semantic hits for the query (empty when "
            "embeddings are unavailable). Writes nothing."
        )
    )
    async def ubongo_recall(query: str = "") -> service.RecallResult:
        # Worker thread too: recall can make a blocking embeddings call.
        return await anyio.to_thread.run_sync(
            functools.partial(service.recall_view, query)
        )

    @app.resource(
        "ubongo://vault/daily/today",
        description="Today's daily note (Obsidian-compatible markdown), verbatim.",
        mime_type="text/markdown",
    )
    def daily_note() -> str:
        return service.daily_note_text()

    @app.resource(
        "ubongo://audit",
        description="Tail of the unified audit log (governance / evolution / sync / authoring).",
        mime_type="text/markdown",
    )
    def audit_log() -> str:
        return service.audit_text()

    return app


def run(http: bool = False, port: int = 8765, addr: str = "0.0.0.0") -> int:
    """Entrypoint used by `ubongo mcp`. stdio by default; --http serves
    streamable HTTP on addr:port for LAN clients (e.g. Compendium)."""
    app = build_server(host=addr, port=port)
    app.run(transport="streamable-http" if http else "stdio")
    return 0
