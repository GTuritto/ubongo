from __future__ import annotations

import logging
import sys

from ubongo import classifier, context, events, memory, router, skills  # noqa: F401  -- memory registers handlers
from ubongo.agents import personas
from ubongo.config import load_config
from ubongo.context import build_system_prompt
from ubongo.delivery import queue
from ubongo.llm import LLMError, complete
from ubongo.memory import store

logger = logging.getLogger("ubongo.repl")

DEFAULT_PERSONA = "architect"
VALID_PERSONAS = ("architect", "operator", "casual")
SUMMARY_PERSONA = "operator"
SUMMARY_SKILL = "summarize-conversation"

_BANNER = "Ubongo REPL ready. /exit to quit."
_AUTO_ENABLED = "Auto routing enabled."
_LLM_FAILURE_MESSAGE = "Sorry, I couldn't reach the model. Check the logs."
_HELP_COMMANDS = (
    "Try /architect, /operator, /casual, /auto, /skill <name>, /skills, /summary, /reload, /exit."
)


def _build_message_history(conv_id: int, current_message: str) -> tuple[str | None, list[dict]]:
    """Returns (summary_text or None, messages list ending with the current user turn)."""
    ctx = store.recall(conv_id)
    history: list[dict] = []
    for msg in ctx.messages:
        if msg.role in ("user", "assistant"):
            history.append({"role": msg.role, "content": msg.content})
    history.append({"role": "user", "content": current_message})
    return ctx.summary_text, history


def _call_llm(
    persona_name: str,
    message: str,
    conv_id: int,
    skill_name: str | None = None,
) -> tuple[str, bool, int, int, str]:
    """Returns (text, ok, tokens_in, tokens_out, model)."""
    persona = personas.get(persona_name)
    summary_text, history = _build_message_history(conv_id, message)
    base = build_system_prompt(persona_name, skill=skill_name)
    system_prompt = (
        base
        if not summary_text
        else f"{base}\n\n## Conversation summary so far\n\n{summary_text}"
    )
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
    pending_skill: str | None = None,
) -> tuple[str, bool, str, str | None, queue.DeliveryToken]:
    """Run a turn end-to-end up to the print boundary.

    Returns (text, ok, used_persona, skill_name, delivery_token). The caller is
    expected to print(text) then call queue.flush_delivered(delivery_token) to
    fire after_send and mark the queue row delivered.
    """
    chosen = persona_name
    suggested_skill: str | None = None
    if auto_mode:
        classification = classifier.classify(message)
        suggested = router.route(classification)
        chosen = router.apply_hysteresis(persona_name, suggested, classification.confidence)
        suggested_skill = classification.suggested_skill
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
                "suggested_skill": suggested_skill,
            },
        )

    resolved_skill = skills.resolve(pinned=pending_skill, suggested=suggested_skill)
    skill_name = resolved_skill.name if resolved_skill else None

    conv_id = store.current_or_new_conversation(chosen)
    user_msg_id = store.append_message(conv_id, "user", message, persona=chosen)

    text, ok, tokens_in, tokens_out, model = _call_llm(chosen, message, conv_id, skill_name)

    assistant_msg_id = None
    if ok:
        assistant_msg_id = store.append_message(
            conv_id,
            "assistant",
            text,
            persona=chosen,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
    ts_now = store.now_iso()
    store.upsert_session(
        active_persona=chosen,
        current_conversation_id=conv_id,
        last_message_at=ts_now,
        auto_mode=auto_mode,
    )

    after_send_payload: dict | None = None
    if ok:
        after_send_payload = {
            "user_message": message,
            "response": text,
            "persona": chosen,
            "auto_routed": auto_mode,
            "conversation_id": conv_id,
            "user_message_id": user_msg_id,
            "assistant_message_id": assistant_msg_id,
            "ts": ts_now,
        }
    token = queue.enqueue_for_delivery(
        text,
        source="response" if ok else "error",
        after_send_payload=after_send_payload,
        metadata={
            "persona": chosen,
            "auto_routed": auto_mode,
            "conversation_id": conv_id,
            "assistant_message_id": assistant_msg_id,
        },
    )
    return text, ok, chosen, skill_name, token


def _render_skills_table() -> str:
    registered = skills.list_skills()
    if not registered:
        return "No skills registered."
    lines = ["Registered skills:"]
    for s in registered:
        lines.append(f"- {s.name} (risk={s.risk}, reversibility={s.reversibility}) — {s.description}")
    return "\n".join(lines)


def _reload_all() -> str:
    context.reload()
    personas.reload()
    skills.reload()
    return "Reloaded UBONGO.md, personas, and skills."


def _render_transcript(messages) -> str:
    lines: list[str] = []
    for m in messages:
        if m.role == "user":
            lines.append(f"User: {m.content}")
        elif m.role == "assistant":
            lines.append(f"Ubongo: {m.content}")
    return "\n\n".join(lines)


def _run_summary() -> str:
    session = store.get_session()
    if session is None or session.current_conversation_id is None:
        return "Not enough conversation yet to summarize."

    config = load_config()
    recall_turns = int(config.get("memory", {}).get("recall_turns", 10))
    messages = store.last_n_messages(session.current_conversation_id, recall_turns)
    if len(messages) < 2:
        return "Not enough conversation yet to summarize."

    transcript = _render_transcript(messages)
    template = skills.prompt(SUMMARY_SKILL, "summarize")
    user_prompt = template.replace("{transcript}", transcript)
    system_prompt = build_system_prompt(SUMMARY_PERSONA, skill=SUMMARY_SKILL)
    persona = personas.get(SUMMARY_PERSONA)
    try:
        result = complete(
            system_prompt,
            [{"role": "user", "content": user_prompt}],
            persona.model,
            persona.max_tokens,
        )
    except LLMError as exc:
        logger.error(
            "summary_llm_error",
            extra={"model": persona.model, "cause": str(exc.cause) if exc.cause else None},
        )
        return _LLM_FAILURE_MESSAGE
    logger.info(
        "summary_turn",
        extra={
            "model": result.model,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "latency_ms": result.latency_ms,
            "transcript_messages": len(messages),
        },
    )
    return result.text


def _parse_skill_command(line: str) -> str | None:
    """Returns the skill name from a `/skill <name>` command, or None if malformed."""
    raw = line.strip().lstrip("/")
    parts = raw.split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "skill":
        return None
    return parts[1].strip()


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
        return current_persona, True, f"Empty command. {_HELP_COMMANDS}", None
    return current_persona, True, f"Unknown command: /{cmd}. {_HELP_COMMANDS}", None


def run(default_persona: str = DEFAULT_PERSONA) -> int:
    session = store.get_session()
    if session and session.active_persona in VALID_PERSONAS:
        persona = session.active_persona
        auto_mode = session.auto_mode
    else:
        persona = default_persona
        auto_mode = False
    pending_skill: str | None = None
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
            head = stripped.lstrip("/").split(maxsplit=1)[0].lower() if stripped.lstrip("/") else ""
            if head == "summary":
                print(_run_summary())
                continue
            if head == "skills":
                print(_render_skills_table())
                continue
            if head == "reload":
                print(_reload_all())
                continue
            if head == "skill":
                requested = _parse_skill_command(stripped)
                if not requested:
                    print(f"Usage: /skill <name>. {_HELP_COMMANDS}")
                elif not skills.has(requested):
                    print(f"Unknown skill: {requested}.")
                else:
                    pending_skill = requested
                    print(f"Next turn will use skill: {requested}.")
                continue

            persona, keep_going, msg, auto_change = handle_slash(stripped, persona)
            print(msg)
            if auto_change is not None:
                auto_mode = auto_change
            store.upsert_session(active_persona=persona, auto_mode=auto_mode)
            if not keep_going:
                return 0
            continue

        text, _ok, used_persona, _skill_used, token = handle_text(
            persona, stripped, auto_mode, pending_skill=pending_skill
        )
        pending_skill = None  # one-shot
        print(text)
        queue.flush_delivered(token)
        if auto_mode:
            persona = used_persona


if __name__ == "__main__":
    sys.exit(run())
