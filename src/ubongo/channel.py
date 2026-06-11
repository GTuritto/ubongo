"""The channel core — the one turn envelope every front shares (candidate 14).

A **channel** (REPL, one-shot, web, MCP, and v0.2's Telegram) is a presentation
layer over the same contract: bootstrap once, run the turn through the one
orchestration seam (`master.handle`), flush the outbound queue (ADR-0002/0003 —
no bypass), and honor the `UBONGO_PROFILE` startup knob identically. Before
this module, each channel re-implemented that envelope by hand; this module is
the envelope, once. Channels keep only what genuinely differs: printing and
exit codes (one-shot), rendering and Approve/Deny (web), TypedDict shaping and
the worker-thread hop (MCP), interactive prompts and per-session toggles
(REPL).

`master.handle` is resolved at call time, so tests that patch the shared
`master` module attribute keep working across every channel.
"""

from __future__ import annotations

import logging
import os

from ubongo import master, profiling
from ubongo.config import load_config
from ubongo.delivery import queue
from ubongo.logging import setup_logging

logger = logging.getLogger("ubongo.channel")

_bootstrapped = False
# Resolved once at bootstrap from UBONGO_PROFILE; only "cpu" applies here (mem
# is REPL-only — the other channels have no report surface).
_startup_profile: str | None = None


def bootstrap(channel: str = "channel") -> dict:
    """Config + logging once + startup-profile knob resolution. Idempotent.
    Starts NO background daemons — channels are the turn path only; daemons
    are the REPL's job."""
    global _bootstrapped, _startup_profile
    config = load_config()
    if not _bootstrapped:
        setup_logging(config["logging"]["level"])
        _startup_profile = profiling.resolve_startup_profile(
            None, os.environ.get("UBONGO_PROFILE")
        )
        if cpu_armed():
            logger.info("channel_cpu_profiling_on", extra={"channel": channel})
        _bootstrapped = True
    return config


def cpu_armed() -> bool:
    """Whether the bootstrap knob armed CPU profiling for this process."""
    return _startup_profile in ("cpu", "all")


def run_turn(
    message: str,
    persona: str,
    *,
    auto_mode: bool = False,
    approved: bool = False,
    pending_skill: str | None = None,
    pending_workflow: str | None = None,
    profile_cpu: bool | None = None,
) -> "tuple[master.Response, str | None]":
    """Run one full turn: optional cProfile wrap, `master.handle`, queue flush.

    `profile_cpu=None` means "use the bootstrap knob"; the REPL passes its own
    per-session toggle instead. Returns (response, cpu_report) — the report's
    first line is already logged here uniformly; fronts that can display the
    full text (REPL, one-shot) use the returned value, the others drop it.
    """
    wrap = cpu_armed() if profile_cpu is None else profile_cpu
    if wrap:
        response, cpu_report = profiling.profile_call(
            master.handle, message, persona, auto_mode=auto_mode,
            pending_skill=pending_skill, pending_workflow=pending_workflow,
            approved=approved,
        )
        if cpu_report:
            logger.info("turn_cpu_profile",
                        extra={"report": cpu_report.splitlines()[0]})
    else:
        response = master.handle(
            message, persona, auto_mode=auto_mode,
            pending_skill=pending_skill, pending_workflow=pending_workflow,
            approved=approved,
        )
        cpu_report = None
    queue.flush_delivered(response.delivery_token)
    return response, cpu_report
