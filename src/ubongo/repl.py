from __future__ import annotations

import logging
import sys

from ubongo import context, events, master, memory, runner, skills  # noqa: F401  -- memory registers handlers
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
    "Try /architect, /operator, /casual, /auto, /skill <name>, /skills, /summary, /queue, /decisions, /policy, /agents, /trace, /exec <cmd>, /mode <workflow>, /optimize <target>, /reload, /exit."
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


def _parse_trace_command(line: str) -> int | None:
    """Returns N from `/trace [N]`. Defaults to 1; returns None for malformed args."""
    raw = line.strip().lstrip("/")
    parts = raw.split(maxsplit=1)
    if parts[0].lower() != "trace":
        return None
    if len(parts) == 1:
        return 1
    try:
        n = int(parts[1].strip())
    except ValueError:
        return None
    return n if n > 0 else None


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
    rows = store.last_n_governance_decisions(n)
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


_OPTIMIZE_LIST_SENTINEL = "__list__"


def _parse_optimize_command(line: str) -> str | None:
    """Returns the target from `/optimize <target>`, the sentinel "__list__"
    for `/optimize` (no arg, lists targets), or None for other commands."""
    raw = line.strip().lstrip("/")
    parts = raw.split(maxsplit=1)
    if parts[0].lower() != "optimize":
        return None
    if len(parts) == 1 or not parts[1].strip():
        return _OPTIMIZE_LIST_SENTINEL
    return parts[1].strip()


def _render_optimize_targets() -> str:
    """Phase 16d: list every evolvable target."""
    from ubongo.evolution import targets

    names = targets.evolvable_targets()
    if not names:
        return "No evolvable targets."
    lines = ["Evolvable targets:"]
    lines.extend(f"  {name}" for name in names)
    lines.append("Run /optimize <target> to generate variants.")
    return "\n".join(lines)


def _render_optimize(target: str) -> str:
    """Generate, persist, and render variants for one target (Phase 16d).

    A direct tool like /exec: no master.handle, no governance, no enqueue. The
    variants are written to evolution_lineage and previewed here.
    """
    from ubongo.config import load_evolution
    from ubongo.evolution import generator, lineage
    from ubongo.evolution.targets import UnknownTargetError

    try:
        n = int(load_evolution().get("population_size", 8))
    except (TypeError, ValueError):
        n = 8

    try:
        variants = generator.generate(target, n)
    except UnknownTargetError:
        return f"Unknown target: {target}.\n{_render_optimize_targets()}"

    if not variants:
        return f"No variants generated for {target} (generator produced none)."

    ids = lineage.record_variants(target, variants)
    generation = lineage.next_generation(target) - 1  # record_variants just used this

    header = (
        f"{len(variants)} variant(s) for {target}, generation {generation} "
        f"(requested {n})."
    )
    lines = [header]
    for idx, (variant, row_id) in enumerate(zip(variants, ids), start=1):
        preview = " ".join(variant.text.split())
        if len(preview) > 100:
            preview = preview[:97] + "..."
        extra = ""
        if variant.strategy == "perturb_temperature":
            extra = f" (Δtemp={variant.metadata.get('temperature_delta')})"
        elif variant.strategy == "recombine":
            extra = f" (peer={variant.metadata.get('peer')})"
        lines.append(f"  [{idx}] #{row_id} {variant.strategy}{extra}: {preview}")
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
    rows = store.last_n_workflow_runs(n)
    if not rows:
        return "No traces yet."
    blocks: list[str] = [f"Recent traces (last {n}):"]
    for r in rows:
        cls = r["classification"] or {}
        wf = r["workflow"] or {}
        agents = wf.get("agents") or []
        gov = r["governance"]
        header = (
            f"--- workflow_run #{r['id']} "
            f"(conv {r['conversation_id']}, msg {r['message_id']}) ---"
        )
        timing = (
            f"started: {_format_time(r['started_at'])}  "
            f"ended: {_format_time(r['ended_at'])}  "
            f"outcome: {r['outcome']}"
        )
        cls_line = (
            "classification: "
            f"intent={cls.get('intent', '—')} "
            f"tone={cls.get('tone', '—')} "
            f"task_type={cls.get('task_type', '—')} "
            f"risk={cls.get('risk', '—')} "
            f"confidence={cls.get('confidence', '—')}"
        )
        wf_line = (
            "workflow: "
            f"persona={wf.get('persona', '—')} "
            f"mode={r['execution_mode']} "
            f"agents=[{','.join(agents)}]"
        )
        # Phase 13e: group repair_runs by the agent they apply to so each
        # affected agent_run row gets indented repair lines beneath it.
        repair_runs_by_agent: dict[str, list[dict]] = {}
        for rr in r.get("repair_runs", []) or []:
            repair_runs_by_agent.setdefault(rr["agent"], []).append(rr)

        agent_lines = ["agents:"]
        if not r["agent_runs"]:
            agent_lines.append("  (no agent runs)")
        # Track which agent names have had their repair lines printed so we
        # only attach them under the FAILING agent_runs row (the original
        # failure), not under the peer's success row.
        printed_repairs: set[str] = set()
        for ar in r["agent_runs"]:
            name = (ar["agent"] or "—")[:14]
            model = (ar["model"] or "—")[:30]
            outcome = ar["outcome"]
            tin = ar["tokens_in"] or 0
            tout = ar["tokens_out"] or 0
            latency = ar["latency_ms"] if ar["latency_ms"] is not None else 0
            conf = "—" if ar["confidence"] is None else f"{ar['confidence']:.2f}"
            err_suffix = f"  err={ar['error']}" if ar.get("error") else ""
            retry_suffix = "  (retried)" if ar.get("retried") else ""
            agent_lines.append(
                f"  {name:<14}  {model:<30}  {outcome:<8}  "
                f"{latency:>5}ms  in={tin}/out={tout}  conf={conf}{err_suffix}{retry_suffix}"
            )
            # Inline repair_runs under the FAILED row for this agent.
            agent_real_name = ar["agent"]
            if (
                outcome == "failure"
                and agent_real_name in repair_runs_by_agent
                and agent_real_name not in printed_repairs
            ):
                for rr in repair_runs_by_agent[agent_real_name]:
                    repair_line = (
                        f"    repair: kind={rr['failure_kind']}  "
                        f"strategy={rr['strategy_attempted']}  "
                        f"outcome={rr['outcome']}"
                    )
                    if rr.get("peer_agent"):
                        repair_line += f"  peer={rr['peer_agent']}"
                    if rr.get("override_model"):
                        repair_line += f"  model={rr['override_model'][:24]}"
                    agent_lines.append(repair_line)
                printed_repairs.add(agent_real_name)
        if gov:
            conf = "—" if gov["confidence"] is None else f"{gov['confidence']:.2f}"
            gov_line = (
                f"governance: action={gov['action']}  conf={conf}  "
                f"intent={gov.get('intent') or '—'}  risk={gov.get('risk') or '—'}  "
                f"rev={gov.get('reversibility') or '—'}"
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


def run(default_persona: str = DEFAULT_PERSONA) -> int:
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
            if head == "policy":
                print(_render_policy())
                continue
            if head == "agents":
                print(_render_agents_table())
                continue
            if head == "trace":
                n = _parse_trace_command(stripped)
                if n is None:
                    print(f"Usage: /trace [N]. {_HELP_COMMANDS}")
                else:
                    print(_render_trace(n))
                continue
            if head == "exec":
                cmd = _parse_exec_command(stripped)
                if cmd is None:
                    print(f"Usage: /exec <cmd>. {_HELP_COMMANDS}")
                else:
                    print(_render_exec(cmd))
                continue
            if head == "mode":
                from ubongo import router as _router
                arg = _parse_mode_command(stripped)
                if arg is None:
                    print(f"Usage: /mode <workflow_name> | /mode list. {_HELP_COMMANDS}")
                elif arg == _MODE_LIST_SENTINEL:
                    print(_render_mode_list())
                elif arg not in _router.workflow_names():
                    print(f"Unknown workflow: {arg}.")
                else:
                    pending_workflow = arg
                    print(f"Next turn will use workflow: {arg}.")
                continue
            if head == "optimize":
                arg = _parse_optimize_command(stripped)
                if arg is None or arg == _OPTIMIZE_LIST_SENTINEL:
                    print(_render_optimize_targets())
                else:
                    print(_render_optimize(arg))
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

        response = master.handle(
            stripped, persona, auto_mode,
            pending_skill=pending_skill,
            pending_workflow=pending_workflow,
        )
        pending_skill = None  # one-shot
        pending_workflow = None  # one-shot
        print(response.text)
        queue.flush_delivered(response.delivery_token)
        if auto_mode:
            persona = response.persona

        # Phase 13f: when Repair gave up, prompt for one-shot retry.
        if response.requires_user_decision:
            choice = _prompt_repair_retry()
            if choice == "y":
                retry_response = master.handle(
                    stripped, persona, auto_mode,
                    pending_skill=None,
                    pending_workflow=None,
                )
                print(retry_response.text)
                queue.flush_delivered(retry_response.delivery_token)
                if auto_mode:
                    persona = retry_response.persona
            # On "n" (or anything else), just continue the loop. The user
            # types the next prompt as usual.

        # Phase 15: when governance held the turn for approval, prompt y/n/why.
        if response.approval is not None:
            choice = _prompt_approval(response.approval)
            store.update_governance_decision(response.approval["decision_id"], choice)
            if choice == "y":
                approved_response = master.handle(
                    stripped, persona, auto_mode,
                    pending_skill=None, pending_workflow=None, approved=True,
                )
                print(approved_response.text)
                queue.flush_delivered(approved_response.delivery_token)
                if auto_mode:
                    persona = approved_response.persona
            else:
                print("Aborted; nothing was done.")


if __name__ == "__main__":
    sys.exit(run())
