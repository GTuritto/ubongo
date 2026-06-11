"""The web channel's turn helper — Streamlit-free so it can be unit-tested.

`run_turn` is the web equivalent of `oneshot.run`: it delegates to the channel
core (`ubongo.channel`), which owns the shared envelope — the cProfile wrap
behind the `UBONGO_PROFILE` knob, the one orchestration seam (`master.handle`),
and the outbound-queue flush. No bypass — every turn still runs classify ->
plan -> execute -> govern -> compose -> enqueue (ADR-0002/0003). The UI in
`app.py` renders the returned `Response` (including the governance approval
gate and the repair-exhausted case).
"""

from __future__ import annotations

import logging

from ubongo import channel, master  # noqa: F401  -- master kept as the test patch target
from ubongo.delivery import queue  # noqa: F401  -- test patch target (shared module attr)

logger = logging.getLogger("ubongo.web")


def bootstrap() -> dict:
    """Load config + configure logging once (the web app's equivalent of the
    work `__main__` does for the CLI). Idempotent; starts NO background
    daemons. Delegates to the channel core."""
    return channel.bootstrap("web")


def run_turn(
    message: str,
    persona: str,
    *,
    auto_mode: bool,
    approved: bool = False,
) -> "master.Response":
    """Run one turn through the channel core and return the `Response` for the
    UI to render. When `approved=True`, re-issues a previously gated turn (the
    web equivalent of the REPL's `y`). The CPU report, when the knob is armed,
    is logged by the core; the web page renders only the response."""
    response, _cpu_report = channel.run_turn(
        message, persona, auto_mode=auto_mode, approved=approved
    )
    return response
