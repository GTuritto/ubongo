from __future__ import annotations

import logging
import sys

from ubongo import master, memory  # noqa: F401  -- registers after_llm seam
from ubongo.delivery import queue
from ubongo.repl import DEFAULT_PERSONA, VALID_PERSONAS

logger = logging.getLogger("ubongo.oneshot")


def run(message: str, persona: str | None = None) -> int:
    chosen = persona or DEFAULT_PERSONA
    if chosen not in VALID_PERSONAS:
        valid = ", ".join(VALID_PERSONAS)
        print(
            f"Error: unknown persona '{chosen}'. Choose from: {valid}.",
            file=sys.stderr,
        )
        return 1

    response = master.handle(message, chosen, auto_mode=False)
    print(response.text)
    queue.flush_delivered(response.delivery_token)
    # Phase 15: one-shot is non-interactive — a turn held for approval cannot
    # be approved here. Print the gated message and exit non-zero; the user
    # re-runs (or uses the REPL to approve).
    if response.approval is not None:
        return 1
    return 0 if response.ok else 1
