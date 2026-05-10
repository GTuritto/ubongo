from __future__ import annotations

import logging
import sys

from ubongo.agents import personas
from ubongo.context import build_system_prompt
from ubongo.llm import LLMError, complete

logger = logging.getLogger("ubongo.repl")

_LLM_FAILURE_MESSAGE = "Sorry, I couldn't reach the model. Check the logs."

DEFAULT_PERSONA = "architect"
VALID_PERSONAS = ("architect", "operator", "casual")

_BANNER = "Ubongo REPL ready. /exit to quit."
_AUTO_NOTICE = "Auto routing not yet active (Phase 3); using default persona: architect."


def handle_text(persona_name: str, message: str) -> str:
    persona = personas.get(persona_name)
    system_prompt = build_system_prompt(persona_name)
    messages = [{"role": "user", "content": message}]
    try:
        result = complete(system_prompt, messages, persona.model, persona.max_tokens)
    except LLMError as exc:
        logger.error(
            "llm_error",
            extra={
                "persona": persona_name,
                "model": persona.model,
                "cause": str(exc.cause) if exc.cause else None,
            },
        )
        return _LLM_FAILURE_MESSAGE

    logger.info(
        "repl_turn",
        extra={
            "persona": persona_name,
            "length": len(message),
            "model": result.model,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "latency_ms": result.latency_ms,
            "attempts": result.attempts,
        },
    )
    return result.text


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

        print(handle_text(persona, stripped))


if __name__ == "__main__":
    sys.exit(run())
