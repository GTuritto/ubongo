from __future__ import annotations

import logging
import sys

from ubongo import classifier, router
from ubongo.agents import personas
from ubongo.context import build_system_prompt
from ubongo.llm import LLMError, complete

logger = logging.getLogger("ubongo.repl")

DEFAULT_PERSONA = "architect"
VALID_PERSONAS = ("architect", "operator", "casual")

_BANNER = "Ubongo REPL ready. /exit to quit."
_AUTO_ENABLED = "Auto routing enabled."
_LLM_FAILURE_MESSAGE = "Sorry, I couldn't reach the model. Check the logs."


def _call_llm(persona_name: str, message: str) -> tuple[str, bool]:
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
        return _LLM_FAILURE_MESSAGE, False

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
    return result.text, True


def handle_text(
    persona_name: str,
    message: str,
    auto_mode: bool = False,
) -> tuple[str, bool, str]:
    if not auto_mode:
        text, ok = _call_llm(persona_name, message)
        return text, ok, persona_name

    classification = classifier.classify(message)
    suggested = router.route(classification)
    chosen = router.apply_hysteresis(persona_name, suggested, classification.confidence)
    logger.info(
        "classify",
        extra={
            "intent": classification.intent,
            "tone": classification.tone,
            "task_type": classification.task_type,
            "risk": classification.risk,
            "confidence": classification.confidence,
            "suggested": suggested,
            "used": chosen,
        },
    )
    text, ok = _call_llm(chosen, message)
    return text, ok, chosen


def handle_slash(line: str, current_persona: str) -> tuple[str, bool, str, bool | None]:
    """Parse a slash command. Returns (new_persona, keep_going, message, auto_mode_change).

    auto_mode_change: True to enable auto, False to disable, None for no change.
    """
    raw = line.strip().lstrip("/").lower()
    cmd = raw.split(maxsplit=1)[0] if raw else ""

    if cmd in VALID_PERSONAS:
        return cmd, True, f"Switched to {cmd}.", False
    if cmd == "auto":
        return current_persona, True, _AUTO_ENABLED, True
    if cmd == "exit":
        return current_persona, False, "Goodbye.", None
    if cmd == "":
        return current_persona, True, "Empty command. Try /architect, /operator, /casual, /auto, /exit.", None
    return current_persona, True, f"Unknown command: /{cmd}. Try /architect, /operator, /casual, /auto, /exit.", None


def run(default_persona: str = DEFAULT_PERSONA) -> int:
    persona = default_persona
    auto_mode = False
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
            persona, keep_going, msg, auto_change = handle_slash(stripped, persona)
            print(msg)
            if auto_change is not None:
                auto_mode = auto_change
            if not keep_going:
                return 0
            continue

        text, _ok, used_persona = handle_text(persona, stripped, auto_mode)
        print(text)
        if auto_mode:
            persona = used_persona


if __name__ == "__main__":
    sys.exit(run())
