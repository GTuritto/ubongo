from __future__ import annotations

import logging
import sys

from ubongo import classifier, memory, router  # noqa: F401  -- memory registers after_llm
from ubongo.agents import personas
from ubongo.context import build_system_prompt
from ubongo.llm import LLMError, complete
from ubongo.memory import store

logger = logging.getLogger("ubongo.repl")

DEFAULT_PERSONA = "architect"
VALID_PERSONAS = ("architect", "operator", "casual")

_BANNER = "Ubongo REPL ready. /exit to quit."
_AUTO_ENABLED = "Auto routing enabled."
_LLM_FAILURE_MESSAGE = "Sorry, I couldn't reach the model. Check the logs."


def _build_message_history(conv_id: int, current_message: str) -> tuple[str | None, list[dict]]:
    """Returns (summary_text or None, messages list ending with the current user turn)."""
    ctx = store.recall(conv_id)
    history: list[dict] = []
    for msg in ctx.messages:
        if msg.role in ("user", "assistant"):
            history.append({"role": msg.role, "content": msg.content})
    history.append({"role": "user", "content": current_message})
    return ctx.summary_text, history


def _system_prompt_with_summary(persona_name: str, summary_text: str | None) -> str:
    base = build_system_prompt(persona_name)
    if not summary_text:
        return base
    return f"{base}\n\n## Conversation summary so far\n\n{summary_text}"


def _call_llm(persona_name: str, message: str, conv_id: int) -> tuple[str, bool, int, int, str]:
    """Returns (text, ok, tokens_in, tokens_out, model)."""
    persona = personas.get(persona_name)
    summary_text, history = _build_message_history(conv_id, message)
    system_prompt = _system_prompt_with_summary(persona_name, summary_text)
    try:
        result = complete(system_prompt, history, persona.model, persona.max_tokens)
    except LLMError as exc:
        logger.error(
            "llm_error",
            extra={
                "persona": persona_name,
                "model": persona.model,
                "cause": str(exc.cause) if exc.cause else None,
            },
        )
        return _LLM_FAILURE_MESSAGE, False, 0, 0, persona.model

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
    return result.text, True, result.tokens_in, result.tokens_out, result.model


def handle_text(
    persona_name: str,
    message: str,
    auto_mode: bool = False,
) -> tuple[str, bool, str]:
    chosen = persona_name
    if auto_mode:
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

    conv_id = store.current_or_new_conversation(chosen)
    store.append_message(conv_id, "user", message, persona=chosen)

    text, ok, tokens_in, tokens_out, model = _call_llm(chosen, message, conv_id)

    if ok:
        store.append_message(
            conv_id,
            "assistant",
            text,
            persona=chosen,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
    store.upsert_session(
        active_persona=chosen,
        current_conversation_id=conv_id,
        last_message_at=store.now_iso(),
        auto_mode=auto_mode,
    )
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
    session = store.get_session()
    if session and session.active_persona in VALID_PERSONAS:
        persona = session.active_persona
        auto_mode = session.auto_mode
    else:
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
            store.upsert_session(active_persona=persona, auto_mode=auto_mode)
            if not keep_going:
                return 0
            continue

        text, _ok, used_persona = handle_text(persona, stripped, auto_mode)
        print(text)
        if auto_mode:
            persona = used_persona


if __name__ == "__main__":
    sys.exit(run())
