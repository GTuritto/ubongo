from __future__ import annotations

import logging
import sys

logger = logging.getLogger("ubongo.repl")

DEFAULT_PERSONA = "architect"
VALID_PERSONAS = ("architect", "operator", "casual")

_BANNER = "Ubongo REPL ready. /exit to quit."
_AUTO_NOTICE = "Auto routing not yet active (Phase 3); using default persona: architect."


def echo(persona: str, message: str) -> str:
    return f"[{persona}] {message}"


def handle_slash(line: str, current_persona: str) -> tuple[str, bool, str]:
    raw = line.strip().lstrip("/").lower()
    cmd = raw.split(maxsplit=1)[0] if raw else ""

    if cmd in VALID_PERSONAS:
        return cmd, True, f"Switched to {cmd}."
    if cmd == "auto":
        return DEFAULT_PERSONA, True, _AUTO_NOTICE
    if cmd == "exit":
        return current_persona, False, "Goodbye."
    if cmd == "":
        return current_persona, True, "Empty command. Try /architect, /operator, /casual, /auto, /exit."
    return current_persona, True, f"Unknown command: /{cmd}. Try /architect, /operator, /casual, /auto, /exit."


def run(default_persona: str = DEFAULT_PERSONA) -> int:
    persona = default_persona
    print(_BANNER)
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            print("Goodbye.")
            return 0

        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("/"):
            persona, keep_going, msg = handle_slash(stripped, persona)
            print(msg)
            if not keep_going:
                return 0
            continue

        print(echo(persona, stripped))
        logger.info(
            "repl_turn",
            extra={"persona": persona, "length": len(stripped)},
        )


if __name__ == "__main__":
    sys.exit(run())
