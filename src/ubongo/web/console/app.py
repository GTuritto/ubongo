"""The console FastAPI app (v0.6 phase 00) — the only module importing
FastAPI/uvicorn (the optional `[console]` extra, imported lazily in `run`).

Three routes: `GET /` serves the bare console page; `POST /turn` starts a
streamed turn (single-flight) and returns its `stream_id`; `GET /stream/{id}`
is the SSE response. The turn runs on a background thread (see `stream_bridge`);
each blocking queue read is offloaded to a worker thread so the event loop is
never blocked. LAN no-auth posture, like the web/MCP channels (ADR-0023).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger("ubongo.web.console.app")

_HTML = Path(__file__).parent / "console.html"
_STOP = object()


def build_app():
    """Construct the FastAPI app. Assumes the `[console]` extra is installed —
    `run` guards the import and prints a friendly hint when it is not."""
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

    from ubongo import channel
    from ubongo.web.console import stream_bridge

    channel.bootstrap("console")  # config + logging once; starts no daemons
    app = FastAPI(title="Ubongo console")
    page = _HTML.read_text(encoding="utf-8")

    @app.get("/")
    async def index():
        return HTMLResponse(page)

    @app.post("/turn")
    async def turn(request: Request):
        body = await request.json()
        message = (body.get("message") or "").strip()
        persona = body.get("persona") or "operator"
        if not message:
            return JSONResponse({"error": "empty message"}, status_code=400)
        stream_id = stream_bridge.start_turn(message, persona)
        if stream_id is None:
            return JSONResponse({"error": "a turn is already streaming"}, status_code=409)
        return JSONResponse({"stream_id": stream_id})

    @app.get("/stream/{stream_id}")
    async def stream(stream_id: str):
        gen = stream_bridge.event_stream(stream_id)

        async def _aiter():
            # Each event_stream step blocks on a thread-safe queue; offload it so
            # the SSE response never blocks the event loop.
            while True:
                item = await asyncio.to_thread(next, gen, _STOP)
                if item is _STOP:
                    break
                yield item

        return StreamingResponse(_aiter(), media_type="text/event-stream")

    return app


def run(port: int = 8770, addr: str = "0.0.0.0") -> int:
    """Serve the console over the LAN (no auth, like web/MCP). Returns rc 1 with
    a friendly hint when the optional `[console]` extra is absent."""
    try:
        import uvicorn  # noqa: F401
        from fastapi import FastAPI  # noqa: F401
    except ImportError:
        print("The console dependency is not installed.")
        print("Install it with:  ./install.sh --console   (or: uv sync --extra console)")
        return 1
    import uvicorn

    logger.info("console_starting", extra={"addr": addr, "port": port})
    uvicorn.run(build_app(), host=addr, port=port, log_level="warning")
    return 0
