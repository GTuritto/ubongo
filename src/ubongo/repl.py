from __future__ import annotations

import logging
import sys

from ubongo import context, events, master, memory, skills  # noqa: F401  -- memory registers handlers
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
    "Try /architect, /operator, /casual, /auto, /skill <name>, /skills, /summary, /queue, /decisions, /reload, /exit."
)


def _parse_queue_command(line: str) -> int | None:
    """Returns N from `/queue [N]`. Defaults to 10; returns None for malformed args."""
    raw = line.strip().lstrip("/")
    parts = raw.split(maxsplit=1)
    if parts[0].lower() != "queue":
        return None
    if len(parts) == 1:
        return 10
    try:
        n = int(parts[1].strip())
    except ValueError:
        return None
    return n if n > 0 else None


def _parse_decisions_command(line: str) -> int | None:
    """Returns N from `/decisions [N]`. Defaults to 10; returns None for malformed args."""
    raw = line.strip().lstrip("/")
    parts = raw.split(maxsplit=1)
    if parts[0].lower() != "decisions":
        return None
    if len(parts) == 1:
        return 10
    try:
        n = int(parts[1].strip())
    except ValueError:
        return None
    return n if n > 0 else None


def _format_time(ts: str | None) -> str:
    if ts is None:
        return "—"
    # ISO 8601 with millisecond precision: "2026-05-12T15:51:57.123Z"
    return ts[11:19] if len(ts) >= 19 else ts


def _render_queue_table(n: int = 10) -> str:
    rows = queue.last_n(n)
    if not rows:
        return "Queue is empty."
    lines = [f"Recent queue (last {n}):"]
    for r in rows:
        preview = r.content.replace("\n", " ").strip()
        if len(preview) > 60:
            preview = preview[:60] + "…"
        lines.append(
            f"  {r.id:>4}  {_format_time(r.created_at)}  "
            f"{_format_time(r.delivered_at):>8}  "
            f"{r.urgency:>6}  {(r.source or '—'):>8}  {preview}"
        )
    return "\n".join(lines)


def _render_decisions_table(n: int = 10) -> str:
    rows = store.last_n_governance_decisions(n)
    if not rows:
        return "No decisions yet."
    lines = [f"Recent decisions (last {n}):"]
    for r in rows:
        intent = (r["intent"] or "—")[:10]
        persona = (r["persona"] or "—")[:10]
        mode = (r["execution_mode"] or "—")[:10]
        risk = (r["risk"] or "—")[:8]
        conf = "—" if r["confidence"] is None else f"{r['confidence']:.2f}"
        action = r["action"]
        lines.append(
            f"  {r['id']:>4}  {_format_time(r['decided_at'])}  "
            f"{intent:>10}  {persona:>10}  {mode:>10}  "
            f"{risk:>8}  {conf:>5}  {action}"
        )
    return "\n".join(lines)


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
            if head == "queue":
                n = _parse_queue_command(stripped)
                if n is None:
                    print(f"Usage: /queue [N]. {_HELP_COMMANDS}")
                else:
                    print(_render_queue_table(n))
                continue
            if head == "decisions":
                n = _parse_decisions_command(stripped)
                if n is None:
                    print(f"Usage: /decisions [N]. {_HELP_COMMANDS}")
                else:
                    print(_render_decisions_table(n))
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

        response = master.handle(stripped, persona, auto_mode, pending_skill=pending_skill)
        pending_skill = None  # one-shot
        print(response.text)
        queue.flush_delivered(response.delivery_token)
        if auto_mode:
            persona = response.persona


if __name__ == "__main__":
    sys.exit(run())
