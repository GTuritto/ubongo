from __future__ import annotations

import logging
import sys

from ubongo import channel, commands, context, events, master, memory, profiling, runner, skills  # noqa: F401  -- memory registers handlers
from ubongo.agents import personas
from ubongo.commands import Command, ReplState
from ubongo.config import load_config
from ubongo.context import build_system_prompt
from ubongo.delivery import queue
from ubongo.llm import LLMError, complete
from ubongo.memory import store
from ubongo.memory import trace
from ubongo.authoring import commands as authoring_commands
from ubongo.evolution import commands as evolution_commands
from ubongo.memory import commands as memory_commands

# Candidate 18: the subsystem command packs moved out of this module; every
# name below is re-exported because tests (and only tests) import them from
# ubongo.repl. New code should import from the packs.
from ubongo.evolution.commands import (  # noqa: F401
    _EVALUATE_LIST_SENTINEL, _OPTIMIZE_LIST_SENTINEL, _cmd_evaluate,
    _cmd_evolution, _cmd_improvements, _cmd_optimize, _diff_preview,
    _parse_evaluate_command, _parse_evolution_command,
    _parse_improvements_command, _parse_optimize_command, _render_evaluate,
    _render_evaluate_targets, _render_evolution_control,
    _render_evolution_status, _render_improvements_action,
    _render_improvements_list, _render_optimize, _render_optimize_targets,
)
from ubongo.authoring.commands import (  # noqa: F401
    _cmd_author, _cmd_authoring, _cmd_skill_candidates,
    _parse_author_command, _parse_authoring_command,
    _parse_skill_candidates_command, _render_author,
    _render_authoring_control, _render_authoring_status,
    _render_skill_candidates_action, _render_skill_candidates_list,
)
from ubongo.memory.commands import (  # noqa: F401
    _cmd_audit, _cmd_conflicts, _cmd_recall, _parse_audit_command,
    _parse_conflicts_command, _parse_recall_command, _render_audit,
    _render_conflicts_list, _render_conflicts_resolve, _render_recall,
)

logger = logging.getLogger("ubongo.repl")

# Candidate 18: the shared mini-helpers live in ubongo.commands; aliases keep
# the long-standing repl-namespace surface.
_parse_int_arg = commands.parse_int_arg
_format_time = commands.format_time

DEFAULT_PERSONA = "architect"
VALID_PERSONAS = ("architect", "operator", "casual")
SUMMARY_PERSONA = "operator"
SUMMARY_SKILL = "summarize-conversation"

_BANNER = "Ubongo REPL ready. /exit to quit."
_AUTO_ENABLED = "Auto routing enabled."
_LLM_FAILURE_MESSAGE = "Sorry, I couldn't reach the model. Check the logs."
# _HELP_COMMANDS is derived from the COMMANDS registry near the bottom of this
# module (single source of truth for which commands exist), once the registry and
# the persona/exit fallbacks are both known.




def _parse_queue_command(line: str) -> int | None:
    """Returns N from `/queue [N]`. Defaults to 10; returns None for malformed args."""
    return _parse_int_arg(line, "queue", 10)


def _parse_decisions_command(line: str) -> int | None:
    """Returns N from `/decisions [N]`. Defaults to 10; returns None for malformed args."""
    return _parse_int_arg(line, "decisions", 10)


def _parse_trace_command(line: str) -> int | None:
    """Returns N from `/trace [N]`. Defaults to 1; returns None for malformed args."""
    return _parse_int_arg(line, "trace", 1)


_PROFILE_BREAKDOWNS = ("agents", "models", "modes")
_PROFILE_CPU_ACTIONS = ("on", "off", "status")
_PROFILE_MEM_ACTIONS = ("on", "off", "status")


def _parse_profile_command(line: str) -> tuple[str, int | str | None] | None:
    """Parses the `/profile` family (candidates 10 + 11).

    Returns ("summary"|"agents"|"models"|"modes", N|None) where N is the
    optional last-N-runs window (None = all runs), ("cpu", "on"|"off"|"status"),
    or ("mem", "report"|"on"|"off"|"status"). None for malformed input."""
    raw = line.strip().lstrip("/")
    parts = raw.split()
    if not parts or parts[0].lower() != "profile":
        return None
    args = [p.lower() for p in parts[1:]]
    if not args:
        return ("summary", None)
    if args[0] == "cpu":
        if len(args) == 2 and args[1] in _PROFILE_CPU_ACTIONS:
            return ("cpu", args[1])
        return None
    if args[0] == "mem":
        if len(args) == 1:
            return ("mem", "report")
        if len(args) == 2 and args[1] in _PROFILE_MEM_ACTIONS:
            return ("mem", args[1])
        return None
    kind = "summary"
    rest = args
    if args[0] in _PROFILE_BREAKDOWNS:
        kind = args[0]
        rest = args[1:]
    if not rest:
        return (kind, None)
    if len(rest) > 1:
        return None
    try:
        n = int(rest[0])
    except ValueError:
        return None
    return (kind, n) if n > 0 else None


def _parse_exec_command(line: str) -> str | None:
    """Returns the command body from `/exec <cmd>` (everything after the
    command word). None if `/exec` was typed with no argument. The body
    is preserved verbatim — quotes, spaces — so the sandbox can shlex it."""
    raw = line.strip().lstrip("/")
    parts = raw.split(maxsplit=1)
    if parts[0].lower() != "exec":
        return None
    if len(parts) == 1:
        return None
    return parts[1].strip() or None


_MODE_LIST_SENTINEL = "__list__"


def _parse_mode_command(line: str) -> str | None:
    """Returns the workflow name from `/mode <name>`, the sentinel
    "__list__" for `/mode list`, or None for `/mode` (no arg) or other
    commands."""
    raw = line.strip().lstrip("/")
    parts = raw.split(maxsplit=1)
    if parts[0].lower() != "mode":
        return None
    if len(parts) == 1:
        return None
    arg = parts[1].strip()
    if not arg:
        return None
    if arg.lower() == "list":
        return _MODE_LIST_SENTINEL
    return arg




def _render_queue_table(n: int = 10) -> str:
    # Hide command-output rows (source="command", now queued per ADR-0002) so the
    # view stays the assistant-turn history it has always been.
    rows = queue.last_n(n, exclude_sources=("command",))
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


def _render_policy() -> str:
    """Render the governance decision matrix loaded from governance.yaml."""
    from ubongo.config import load_governance

    gov = load_governance()
    thresholds = gov.get("thresholds", {}) or {}
    approval = gov.get("require_approval", {}) or {}
    keywords = gov.get("destructive_keywords", []) or []
    lines = [
        "Governance policy (config/governance.yaml):",
        "  decision matrix (priority order):",
        "    1. risk=destructive                      -> require_approval",
        "    2. risk=high AND reversibility=irreversible -> require_approval",
        "    3. evaluator confidence < reject floor    -> reject",
        "    4. command turn, classifier confidence low -> ask_clarification",
        "    5. otherwise                              -> auto",
        "  thresholds:",
        f"    reject_below_confidence:        {thresholds.get('reject_below_confidence')}",
        f"    clarification_below_confidence: {thresholds.get('clarification_below_confidence')}",
        f"    critic_band:                    {thresholds.get('critic_band')}",
        f"    auto_route_min_confidence:      {thresholds.get('auto_route_min_confidence')}",
        "  require_approval:",
        f"    risks:                  {approval.get('risks')}",
        f"    irreversible_high_risk: {approval.get('irreversible_high_risk')}",
        f"  destructive_keywords ({len(keywords)}): {', '.join(keywords)}",
    ]
    return "\n".join(lines)


def _render_decisions_table(n: int = 10) -> str:
    rows = trace.last_n_governance_decisions(n)
    if not rows:
        return "No decisions yet."
    lines = [f"Recent decisions (last {n}):"]
    for r in rows:
        intent = (r["intent"] or "—")[:10]
        persona = (r["persona"] or "—")[:10]
        mode = (r["execution_mode"] or "—")[:10]
        risk = (r["risk"] or "—")[:8]
        rev = (r.get("reversibility") or "—")[:12]
        conf = "—" if r["confidence"] is None else f"{r['confidence']:.2f}"
        action = r["action"]
        lines.append(
            f"  {r['id']:>4}  {_format_time(r['decided_at'])}  "
            f"{intent:>10}  {persona:>10}  {mode:>10}  "
            f"{risk:>8}  {rev:>12}  {conf:>5}  {action}"
        )
    return "\n".join(lines)


def _render_mode_list() -> str:
    """Phase 12g: list every workflow declared in workflows.yaml with its mode."""
    from ubongo import router
    names = router.workflow_names()
    if not names:
        return "No workflows declared."
    lines = ["Available workflows:"]
    for name in names:
        mode = router.workflow_mode(name)
        agents = ", ".join(router.workflow_agents(name))
        lines.append(f"  {name:<26}  mode={mode:<14}  agents=[{agents}]")
    return "\n".join(lines)


















































def _render_exec(cmd: str) -> str:
    """Phase 11e: debug-only direct sandbox path. Bypasses master.handle —
    no workflow_runs row, no governance, no enqueue, no vault."""
    from ubongo import sandbox
    try:
        result = sandbox.run_constrained(cmd, timeout=10)
    except sandbox.SandboxRefused as exc:
        return f"Refused: {exc}"
    except Exception as exc:
        return f"Error: {type(exc).__name__}: {str(exc)[:200]}"
    cmd_str = " ".join(result.argv)
    return (
        f"$ {cmd_str}\n"
        f"exit={result.exit_code}  ({result.latency_ms}ms)\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def _render_trace(n: int = 1) -> str:
    traces = trace.last_n_workflow_runs(n)
    if not traces:
        return "No traces yet."
    blocks: list[str] = [f"Recent traces (last {n}):"]
    for t in traces:
        gov = t.governance
        header = (
            f"--- workflow_run #{t.id} "
            f"(conv {t.conversation_id}, msg {t.message_id}) ---"
        )
        timing = (
            f"started: {_format_time(t.started_at)}  "
            f"ended: {_format_time(t.ended_at)}  "
            f"outcome: {t.outcome}"
        )
        cls_line = (
            "classification: "
            f"intent={t.intent or '—'} "
            f"tone={t.tone or '—'} "
            f"task_type={t.task_type or '—'} "
            f"risk={t.risk or '—'} "
            f"confidence={t.cls_confidence if t.cls_confidence is not None else '—'}"
        )
        wf_line = (
            "workflow: "
            f"persona={t.persona or '—'} "
            f"mode={t.execution_mode} "
            f"agents=[{','.join(t.agents)}]"
        )
        # The store has already grouped repair attempts under the failing
        # agent_run they apply to; we just render each row and its repairs.
        agent_lines = ["agents:"]
        if not t.agent_runs:
            agent_lines.append("  (no agent runs)")
        for ar in t.agent_runs:
            name = (ar.agent or "—")[:14]
            model = (ar.model or "—")[:30]
            tin = ar.tokens_in or 0
            tout = ar.tokens_out or 0
            latency = ar.latency_ms if ar.latency_ms is not None else 0
            conf = "—" if ar.confidence is None else f"{ar.confidence:.2f}"
            err_suffix = f"  err={ar.error}" if ar.error else ""
            retry_suffix = "  (retried)" if ar.retried else ""
            agent_lines.append(
                f"  {name:<14}  {model:<30}  {ar.outcome:<8}  "
                f"{latency:>5}ms  in={tin}/out={tout}  conf={conf}{err_suffix}{retry_suffix}"
            )
            for rr in ar.repair_runs:
                repair_line = (
                    f"    repair: kind={rr.failure_kind}  "
                    f"strategy={rr.strategy_attempted}  "
                    f"outcome={rr.outcome}"
                )
                if rr.peer_agent:
                    repair_line += f"  peer={rr.peer_agent}"
                if rr.override_model:
                    repair_line += f"  model={rr.override_model[:24]}"
                agent_lines.append(repair_line)
        if gov:
            conf = "—" if gov.confidence is None else f"{gov.confidence:.2f}"
            gov_line = (
                f"governance: action={gov.action}  conf={conf}  "
                f"intent={gov.intent or '—'}  risk={gov.risk or '—'}  "
                f"rev={gov.reversibility or '—'}"
            )
        else:
            gov_line = "governance: (no decision)"
        blocks.append("\n".join([header, timing, cls_line, wf_line, *agent_lines, gov_line]))
    return "\n\n".join(blocks)


def _render_agents_table() -> str:
    registry = runner.default_registry()
    if not registry:
        return "No agents registered."
    lines = ["Registered agents:"]
    for name in sorted(registry):
        agent = registry[name]
        model = getattr(agent, "default_model", "") or "—"
        role = getattr(agent, "role", "")
        lines.append(f"  {name:<22}  {role:<48}  {model}")
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
    # Phase 21e: settings hot-reload. config.reload() clears the shared cache
    # (settings.yaml + governance.yaml) and must run BEFORE personas.reload() so
    # the next persona load reads any changed models.* on the next turn. router
    # reload picks up routing.yaml / workflows.yaml edits.
    from ubongo import config as _config
    from ubongo import router as _router

    _config.reload()
    context.reload()
    personas.reload()
    skills.reload()
    _router.reload()
    return "Reloaded settings, UBONGO.md, personas, skills, and routing."


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


# ---------- command registry (the seam the loop dispatches over) ----------
#
# Each handler takes the full command line + the mutable ReplState and returns
# the text to emit (or None). Handlers are pure — the loop owns I/O (emit routes
# through the notification queue). Adding a command = a handler + a registry
# entry, not an edit to _repl_loop. persona/auto/exit stay in handle_slash (their
# 4-tuple contract is exercised directly by tests); the loop falls back to it when
# a head isn't registered.

def _cmd_summary(line: str, state: ReplState) -> str | None:
    return _run_summary()


def _cmd_skills(line: str, state: ReplState) -> str | None:
    return _render_skills_table()


def _cmd_queue(line: str, state: ReplState) -> str | None:
    n = _parse_queue_command(line)
    return f"Usage: /queue [N]. {_HELP_COMMANDS}" if n is None else _render_queue_table(n)


def _cmd_decisions(line: str, state: ReplState) -> str | None:
    n = _parse_decisions_command(line)
    return f"Usage: /decisions [N]. {_HELP_COMMANDS}" if n is None else _render_decisions_table(n)


def _cmd_policy(line: str, state: ReplState) -> str | None:
    return _render_policy()


def _cmd_agents(line: str, state: ReplState) -> str | None:
    return _render_agents_table()


def _cmd_trace(line: str, state: ReplState) -> str | None:
    n = _parse_trace_command(line)
    return f"Usage: /trace [N]. {_HELP_COMMANDS}" if n is None else _render_trace(n)


def _cmd_exec(line: str, state: ReplState) -> str | None:
    cmd = _parse_exec_command(line)
    return f"Usage: /exec <cmd>. {_HELP_COMMANDS}" if cmd is None else _render_exec(cmd)


_PROFILE_RENDERERS = {
    "summary": profiling.render_summary,
    "agents": profiling.render_agents,
    "models": profiling.render_models,
    "modes": profiling.render_modes,
}


def _cmd_profile(line: str, state: ReplState) -> str | None:
    parsed = _parse_profile_command(line)
    if parsed is None:
        return (
            "Usage: /profile [agents|models|modes] [N] | /profile cpu on|off|status"
            f" | /profile mem [on|off|status]. {_HELP_COMMANDS}"
        )
    kind, arg = parsed
    if kind == "cpu":
        if arg == "on":
            state.cpu_profile = True
            return (
                "CPU profiling on: each turn runs under cProfile "
                f"(reports in {profiling.profiles_dir()})."
            )
        if arg == "off":
            state.cpu_profile = False
            return "CPU profiling off."
        return f"CPU profiling is {'on' if state.cpu_profile else 'off'}."
    if kind == "mem":
        if arg == "on":
            profiling.mem_start()
            return (
                "Memory profiling armed: baseline snapshot taken. tracemalloc adds "
                "per-allocation overhead while on; /profile mem shows growth, "
                "/profile mem off disarms."
            )
        if arg == "off":
            profiling.mem_stop()
            return "Memory profiling off."
        if arg == "status":
            return f"Memory profiling is {'on' if profiling.mem_active() else 'off'}."
        report = profiling.mem_report()
        return report if report is not None else (
            "Memory profiling is off. /profile mem on to take a baseline first."
        )
    return _PROFILE_RENDERERS[kind](arg)


def _cmd_mode(line: str, state: ReplState) -> str | None:
    from ubongo import router as _router
    arg = _parse_mode_command(line)
    if arg is None:
        return f"Usage: /mode <workflow_name> | /mode list. {_HELP_COMMANDS}"
    if arg == _MODE_LIST_SENTINEL:
        return _render_mode_list()
    if arg not in _router.workflow_names():
        return f"Unknown workflow: {arg}."
    state.pending_workflow = arg
    return f"Next turn will use workflow: {arg}."
















def _cmd_reload(line: str, state: ReplState) -> str | None:
    return _reload_all()


def _cmd_skill(line: str, state: ReplState) -> str | None:
    requested = _parse_skill_command(line)
    if not requested:
        return f"Usage: /skill <name>. {_HELP_COMMANDS}"
    if not skills.has(requested):
        return f"Unknown skill: {requested}."
    state.pending_skill = requested
    return f"Next turn will use skill: {requested}."




























COMMANDS: dict[str, Command] = {
    "skill":        Command(_cmd_skill, "/skill <name>"),
    "skills":       Command(_cmd_skills, "/skills"),
    "summary":      Command(_cmd_summary, "/summary"),
    "queue":        Command(_cmd_queue, "/queue [N]"),
    "decisions":    Command(_cmd_decisions, "/decisions [N]"),
    "policy":       Command(_cmd_policy, "/policy"),
    "agents":       Command(_cmd_agents, "/agents"),
    "trace":        Command(_cmd_trace, "/trace [N]"),
    "profile":      Command(_cmd_profile, "/profile [agents|models|modes|cpu|mem] [N]"),
    "exec":         Command(_cmd_exec, "/exec <cmd>"),
    "mode":         Command(_cmd_mode, "/mode <workflow>"),
    **evolution_commands.COMMANDS,
    **authoring_commands.COMMANDS,
    **memory_commands.COMMANDS,
    "reload":       Command(_cmd_reload, "/reload"),
}

# Single source of truth for the help banner: derived from the registry plus the
# persona/exit tokens handled by handle_slash.
_HELP_COMMANDS = commands.help_banner(
    COMMANDS, extra=("/architect", "/operator", "/casual", "/auto", "/exit")
)


def emit(text: str) -> None:
    """Route command output through the notification queue (ADR-0002), then print.

    ``after_send_payload=None`` so command output does NOT trigger the vault
    projection (only assistant turns project — single-writer intact). Tagged
    source="command" so /queue (which shows assistant turns) filters it out.
    Interactive prompts stay direct: they are synchronous request/response I/O,
    not deliverable notifications."""
    token = queue.enqueue_for_delivery(text, source="command", after_send_payload=None)
    print(text)
    queue.flush_delivered(token)


def _prompt_repair_retry() -> str:
    """Phase 13f: ask the user whether to retry after Repair exhausted.

    Returns "y" or "n" (anything other than "y" is treated as "n").
    Reads from stdin via input(); on EOF (Ctrl+D / piped input ends),
    returns "n" so the loop doesn't crash."""
    try:
        choice = input("Retry the same message? (y/n) ").strip().lower()
    except EOFError:
        return "n"
    return "y" if choice == "y" else "n"


def _prompt_approval(request: dict) -> str:
    """Phase 15a: ask the user to approve a require_approval turn.

    Prints the one-line summary, then reads y/n/why. `why` prints the
    explanation paragraph and re-prompts. Returns "y" or "n" — anything other
    than "y" (and EOF) is treated as "n"."""
    print(request["summary"])
    while True:
        try:
            choice = input("Approve? (y/n/why) ").strip().lower()
        except EOFError:
            return "n"
        if choice == "why":
            print(request["why"])
            continue
        return "y" if choice == "y" else "n"


def _apply_startup_profile(value: str | None, state: ReplState) -> str | None:
    """Candidate 12: arm the same toggles /profile cpu|mem on control, from the
    --profile flag / UBONGO_PROFILE env knob. Returns the startup notice to
    print, or None when nothing was armed."""
    if value not in profiling.STARTUP_PROFILE_VALUES:
        return None
    armed = []
    if value in ("cpu", "all"):
        state.cpu_profile = True
        armed.append("cpu (cProfile per turn)")
    if value in ("mem", "all"):
        profiling.mem_start()
        armed.append("mem (tracemalloc baseline taken)")
    return (
        f"Profiling armed at startup: {', '.join(armed)}. "
        "Disarm with /profile cpu off | /profile mem off."
    )


def run(default_persona: str = DEFAULT_PERSONA, *, startup_profile: str | None = None) -> int:
    session = store.get_session()
    if session and session.active_persona in VALID_PERSONAS:
        persona = session.active_persona
        auto_mode = session.auto_mode
    else:
        persona = default_persona
        auto_mode = False
    pending_skill: str | None = None
    pending_workflow: str | None = None
    print(_BANNER)

    # Phase 18: start the autonomous GP loop in a background daemon thread when
    # evolution.enabled. It comes up paused (persisted status), so nothing runs
    # until /evolution resume. Stopped on every REPL exit path via finally.
    from ubongo.evolution.loop import EvolutionLoop
    _evolution_loop = EvolutionLoop()
    _evolution_loop.start()

    # Phase 21: start the vault watcher when vault.sync.enabled. Ingests external
    # edits you make in Obsidian; off by default. Stopped on every exit path.
    from ubongo.memory.vault_watch import VaultWatcher
    _vault_watcher = VaultWatcher()
    _vault_watcher.start()

    # Phase 4 (authoring): start the autonomous authoring daemon when
    # authoring.enabled. Boots paused (persisted), throttled by a rolling-hour
    # budget; it only ever drafts into quarantine — approval stays manual.
    from ubongo.authoring.loop import AuthoringLoop
    _authoring_loop = AuthoringLoop()
    _authoring_loop.start()

    try:
        return _repl_loop(
            persona, auto_mode, pending_skill, pending_workflow,
            startup_profile=startup_profile,
        )
    finally:
        _evolution_loop.stop()
        _vault_watcher.stop()
        _authoring_loop.stop()


def _repl_loop(persona, auto_mode, pending_skill, pending_workflow,
               startup_profile: str | None = None) -> int:
    state = ReplState(
        persona=persona, auto_mode=auto_mode,
        pending_skill=pending_skill, pending_workflow=pending_workflow,
    )
    startup_notice = _apply_startup_profile(startup_profile, state)
    if startup_notice:
        print(startup_notice)
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
            head, _rest = commands.split_command(stripped)
            result = commands.dispatch(COMMANDS, head, stripped, state)
            if result is commands.UNKNOWN:
                # persona / auto / exit / unknown — handle_slash owns these; its
                # 4-tuple contract is exercised directly by tests.
                new_persona, keep_going, msg, auto_change = handle_slash(stripped, state.persona)
                state.persona = new_persona
                if auto_change is not None:
                    state.auto_mode = auto_change
                emit(msg)
                store.upsert_session(active_persona=state.persona, auto_mode=state.auto_mode)
                if not keep_going:
                    return 0
            elif result is not None:
                emit(result)
            continue

        # Candidates 10 + 14: the turn envelope (optional cProfile wrap,
        # master.handle resolved at call time, queue flush) is the channel
        # core's. The REPL passes its per-session toggle and keeps only
        # presentation: printing, the report emit, and the prompts below.
        response, cpu_report = channel.run_turn(
            stripped, state.persona, auto_mode=state.auto_mode,
            pending_skill=state.pending_skill,
            pending_workflow=state.pending_workflow,
            profile_cpu=state.cpu_profile,
        )
        state.pending_skill = None  # one-shot
        state.pending_workflow = None  # one-shot
        print(response.text)
        if state.auto_mode:
            state.persona = response.persona
        if cpu_report:
            emit(cpu_report)

        # Phase 13f: when Repair gave up, prompt for one-shot retry.
        if response.requires_user_decision:
            choice = _prompt_repair_retry()
            if choice == "y":
                retry_response, _ = channel.run_turn(
                    stripped, state.persona, auto_mode=state.auto_mode,
                    profile_cpu=False,
                )
                print(retry_response.text)
                if state.auto_mode:
                    state.persona = retry_response.persona
            # On "n" (or anything else), just continue the loop. The user
            # types the next prompt as usual.

        # Phase 15: when governance held the turn for approval, prompt y/n/why.
        if response.approval is not None:
            choice = _prompt_approval(response.approval)
            trace.update_governance_decision(response.approval["decision_id"], choice)
            if choice == "y":
                approved_response, _ = channel.run_turn(
                    stripped, state.persona, auto_mode=state.auto_mode,
                    approved=True, profile_cpu=False,
                )
                print(approved_response.text)
                if state.auto_mode:
                    state.persona = approved_response.persona
            else:
                emit("Aborted; nothing was done.")


if __name__ == "__main__":
    sys.exit(run())
