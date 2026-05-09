from __future__ import annotations

import logging
import sys

from ubongo.repl import DEFAULT_PERSONA, VALID_PERSONAS, echo

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

    print(echo(chosen, message))
    logger.info(
        "oneshot_turn",
        extra={"persona": chosen, "length": len(message)},
    )
    return 0
