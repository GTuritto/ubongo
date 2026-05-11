from __future__ import annotations

import logging
import sys

from ubongo import memory  # noqa: F401  -- registers after_llm seam
from ubongo.repl import DEFAULT_PERSONA, VALID_PERSONAS, handle_text

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

    response, ok, _used, _skill = handle_text(chosen, message, auto_mode=False)
    print(response)
    return 0 if ok else 1
