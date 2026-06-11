from __future__ import annotations

import logging
import sys

from ubongo import master, memory, profiling  # noqa: F401  -- registers after_llm seam
from ubongo.delivery import queue
from ubongo.repl import DEFAULT_PERSONA, VALID_PERSONAS

logger = logging.getLogger("ubongo.oneshot")


def run(message: str, persona: str | None = None,
        profile: str | bool | None = None) -> int:
    chosen = persona or DEFAULT_PERSONA
    if chosen not in VALID_PERSONAS:
        valid = ", ".join(VALID_PERSONAS)
        print(
            f"Error: unknown persona '{chosen}'. Choose from: {valid}.",
            file=sys.stderr,
        )
        return 1

    # Candidates 10 + 12: `ubongo send --profile[=cpu|mem|all]` (or the
    # UBONGO_PROFILE env knob). True normalizes to "cpu" so candidate-10
    # callers are unchanged.
    if profile is True:
        profile = "cpu"
    if profile in ("mem", "all"):
        profiling.mem_start()
    if profile in ("cpu", "all"):
        response, cpu_report = profiling.profile_call(
            master.handle, message, chosen, auto_mode=False
        )
    else:
        response, cpu_report = master.handle(message, chosen, auto_mode=False), None
    print(response.text)
    reports = [r for r in (cpu_report,) if r]
    if profile in ("mem", "all"):
        mem_report = profiling.mem_report()
        if mem_report:
            reports.append(mem_report)
        profiling.mem_stop()
    for report in reports:
        # Mirror repl.emit: command-style output still goes through the
        # notification queue (ADR-0002), tagged source="command".
        token = queue.enqueue_for_delivery(
            report, source="command", after_send_payload=None
        )
        print(report)
        queue.flush_delivered(token)
    queue.flush_delivered(response.delivery_token)
    # Phase 15: one-shot is non-interactive — a turn held for approval cannot
    # be approved here. Print the gated message and exit non-zero; the user
    # re-runs (or uses the REPL to approve).
    if response.approval is not None:
        return 1
    return 0 if response.ok else 1
