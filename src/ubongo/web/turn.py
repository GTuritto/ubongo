"""The web channel's turn helper — Streamlit-free so it can be unit-tested.

`run_turn` is the web equivalent of `oneshot.run`: it calls the one orchestration
seam (`master.handle`) and flushes the outbound queue, exactly like the REPL and
one-shot. No bypass — every turn still runs classify -> plan -> execute -> govern
-> compose -> enqueue (ADR-0002/0003). The UI in `app.py` renders the returned
`Response` (including the governance approval gate and the repair-exhausted case).
"""

from __future__ import annotations

import logging
import os

from ubongo import master, profiling
from ubongo.config import load_config
from ubongo.delivery import queue
from ubongo.logging import setup_logging

logger = logging.getLogger("ubongo.web")

_bootstrapped = False
# Candidate 12: resolved once at bootstrap from UBONGO_PROFILE. Only "cpu"
# applies on the web path (mem is REPL-only — the web UI has no report surface).
_startup_profile: str | None = None


def bootstrap() -> dict:
    """Load config + configure logging once (the web app's equivalent of the work
    `__main__` does for the CLI). Idempotent. The SQLite store and vault bootstrap
    lazily on first use, as in one-shot; the GP loop and vault watcher are NOT
    started here — this is the turn path only."""
    global _bootstrapped, _startup_profile
    config = load_config()
    if not _bootstrapped:
        setup_logging(config["logging"]["level"])
        _startup_profile = profiling.resolve_startup_profile(
            None, os.environ.get("UBONGO_PROFILE")
        )
        if _startup_profile in ("cpu", "all"):
            logger.info("web_cpu_profiling_on")
        _bootstrapped = True
    return config


def run_turn(
    message: str,
    persona: str,
    *,
    auto_mode: bool,
    approved: bool = False,
) -> "master.Response":
    """Run one turn through the Master and flush the outbound queue.

    Returns the `Response` for the UI to render. When `approved=True`, re-issues a
    previously gated turn (the web equivalent of the REPL's `y`)."""
    if _startup_profile in ("cpu", "all"):
        response, cpu_report = profiling.profile_call(
            master.handle, message, persona, auto_mode=auto_mode, approved=approved
        )
        if cpu_report:
            # The artifact is the .prof; the UI stays clean — log the location.
            logger.info("web_turn_cpu_profile",
                        extra={"report": cpu_report.splitlines()[0]})
    else:
        response = master.handle(message, persona, auto_mode=auto_mode, approved=approved)
    queue.flush_delivered(response.delivery_token)
    return response
