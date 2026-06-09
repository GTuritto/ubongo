from __future__ import annotations

import logging
import sys

from ubongo import commands, context, events, master, memory, runner, skills  # noqa: F401  -- memory registers handlers
from ubongo.agents import personas
from ubongo.commands import Command, ReplState
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
# _HELP_COMMANDS is derived from the COMMANDS registry near the bottom of this
# module (single source of truth for which commands exist), once the registry and
# the persona/exit fallbacks are both known.


def _parse_int_arg(line: str, command: str, default: int) -> int | None:
    """Shared parser for the `/<command> [N]` shape: returns N (default when
    omitted), or None for a malformed/non-positive arg. The three int-arg
    commands (/queue, /decisions, /trace) delegate here."""
    raw = line.strip().lstrip("/")
    parts = raw.split(maxsplit=1)
    if parts[0].lower() != command:
        return None
    if len(parts) == 1:
        return default
    try:
        n = int(parts[1].strip())
    except ValueError:
        return None
    return n if n > 0 else None


def _parse_queue_command(line: str) -> int | None:
    """Returns N from `/queue [N]`. Defaults to 10; returns None for malformed args."""
    return _parse_int_arg(line, "queue", 10)


def _parse_decisions_command(line: str) -> int | None:
    """Returns N from `/decisions [N]`. Defaults to 10; returns None for malformed args."""
    return _parse_int_arg(line, "decisions", 10)


def _parse_trace_command(line: str) -> int | None:
    """Returns N from `/trace [N]`. Defaults to 1; returns None for malformed args."""
    return _parse_int_arg(line, "trace", 1)


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
    """Render variants for one target. The generate+persist business logic lives
    in ubongo.evolution.manual; this only formats the result (Phase 16d)."""
    from ubongo.evolution import manual
    from ubongo.evolution.targets import UnknownTargetError

    try:
        out = manual.generate_variants(target)
    except UnknownTargetError:
        return f"Unknown target: {target}.\n{_render_optimize_targets()}"

    if not out.variants:
        return f"No variants generated for {target} (generator produced none)."

    header = (
        f"{len(out.variants)} variant(s) for {out.target}, generation {out.generation} "
        f"(requested {out.requested})."
    )
    lines = [header]
    for idx, (variant, row_id) in enumerate(zip(out.variants, out.ids), start=1):
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


_EVALUATE_LIST_SENTINEL = "__list__"


def _parse_evaluate_command(line: str) -> str | None:
    """Returns the target from `/evaluate <target>`, the sentinel "__list__"
    for `/evaluate` (no arg, lists targets), or None for other commands."""
    raw = line.strip().lstrip("/")
    parts = raw.split(maxsplit=1)
    if parts[0].lower() != "evaluate":
        return None
    if len(parts) == 1 or not parts[1].strip():
        return _EVALUATE_LIST_SENTINEL
    return parts[1].strip()


def _render_evaluate_targets() -> str:
    """Phase 17e: list targets that have at least one generated variant."""
    from ubongo.evolution import targets as _targets
    from ubongo.memory import store as _store

    names = [t for t in _targets.evolvable_targets() if _store.max_lineage_generation(t) > 0]
    if not names:
        return "No evaluable targets. Run /optimize <target> to generate variants first."
    lines = ["Evaluable targets (have variants):"]
    lines.extend(f"  {name}" for name in names)
    lines.append("Run /evaluate <target> to score the latest generation.")
    return "\n".join(lines)


def _render_evaluate(target: str) -> str:
    """Render the fitness leaderboard for a target's latest generation. The
    score+persist business logic lives in ubongo.evolution.manual; this only
    formats the result (Phase 17e)."""
    from ubongo.evolution import manual
    from ubongo.evolution.targets import UnknownTargetError

    try:
        out = manual.score_latest_generation(target)
    except UnknownTargetError:
        return f"Unknown target: {target}.\n{_render_evaluate_targets()}"
    except manual.NoVariantsError:
        return f"No variants for {target}. Run /optimize {target} first."

    result = out.result
    generation = out.generation
    if not result.cohort:
        return (
            f"No variants evaluated for {target} (call budget exhausted before "
            f"any variant, or all samples were dropped). "
            f"{result.skipped}/{result.total_variants} skipped."
        )

    header = (
        f"Leaderboard for {target}, generation {generation} "
        f"(sample_set={result.sample_set_version}; "
        f"{result.evaluated}/{result.total_variants} scored, {result.skipped} skipped):"
    )
    lines = [header]
    for rank, (metrics, fit) in enumerate(out.ranked, start=1):
        strat = out.strategy_by_id.get(metrics.lineage_id) or "?"
        lines.append(
            f"  {rank}. #{metrics.lineage_id} {strat:<20} "
            f"fitness={fit:.3f}  "
            f"success={metrics.success_rate:.2f} "
            f"halluc={metrics.hallucination_rate:.2f} "
            f"corr={metrics.user_correction_rate:.2f} "
            f"cost={metrics.cost:.0f}tok lat={metrics.latency_ms:.0f}ms"
        )
    if result.skipped:
        lines.append(
            f"  ({result.skipped} variant(s) skipped by the call budget; raise "
            f"evolution.max_calls_per_hour or lower samples_per_eval for a fuller run.)"
        )
    return "\n".join(lines)


_EVOLUTION_SUBCOMMANDS = ("status", "pause", "resume", "off")


def _parse_evolution_command(line: str) -> str | None:
    """Returns the subcommand from `/evolution <sub>` (defaults to "status"),
    or None for other commands. Unknown subcommands return the raw token so the
    dispatcher can show usage."""
    raw = line.strip().lstrip("/")
    parts = raw.split(maxsplit=1)
    if parts[0].lower() != "evolution":
        return None
    if len(parts) == 1 or not parts[1].strip():
        return "status"
    return parts[1].strip().split()[0].lower()


def _render_evolution_status() -> str:
    """Phase 18d: render loop control state + per-target progress + throttle."""
    from ubongo.config import load_evolution
    from ubongo.evolution import targets as _targets
    from ubongo.memory import store as _store

    evo = load_evolution()
    enabled = bool(evo.get("enabled", False))
    status = _store.get_evolution_status()
    cap = int(evo.get("max_calls_per_hour", 30))
    used = _store.calls_in_last_hour()
    cron = evo.get("cron")
    pace = "continuous" if cron is None else f"every {cron}s"

    lines = [
        f"Evolution loop: status={status}  enabled={enabled}  "
        f"throttle={used}/{cap} calls in last hour  pacing={pace}",
    ]
    if not enabled:
        lines.append("  (evolution.enabled is false in settings.yaml — the loop thread does not start.)")
    for target in _targets.evolvable_targets():
        gen = _store.max_lineage_generation(target)
        if gen == 0:
            lines.append(f"  {target:<20} no generations yet")
            continue
        evals = _store.evaluations_for_target(target, generation=gen)
        best = evals[0]["fitness"] if evals else None
        best_str = f"best fitness={best:.3f}" if best is not None else "unevaluated"
        last = _store.last_cycle_at(target) or "never"
        lines.append(f"  {target:<20} gen {gen}  {best_str}  last cycle {last}")
    recent = _store.evolution_runs_recent(3)
    if recent:
        lines.append("  recent cycles:")
        for r in recent:
            lines.append(
                f"    #{r['id']} {r['target']} gen{r['generation']} "
                f"{r['outcome']} calls={r['calls_spent']}"
            )
    return "\n".join(lines)


def _render_evolution_control(sub: str) -> str:
    """Phase 18e: apply pause/resume/off and report. resume warns if the loop
    is disabled in settings (the thread never started)."""
    from ubongo.config import load_evolution
    from ubongo.memory import store as _store

    if sub == "resume":
        _store.set_evolution_status("running")
        if not load_evolution().get("enabled", False):
            return ("Status set to running, but evolution.enabled is false in "
                    "settings.yaml so the loop thread is not active. Enable it and "
                    "restart the REPL to run.")
        return "Evolution loop resumed (status=running). Generations will run, throttled."
    if sub == "pause":
        _store.set_evolution_status("paused")
        return "Evolution loop paused. The in-flight cycle finishes; no new ones start."
    if sub == "off":
        _store.set_evolution_status("off")
        return "Evolution loop off. It idles until /evolution resume."
    return f"Unknown subcommand: {sub}. Usage: /evolution status|pause|resume|off."


def _parse_improvements_command(line: str):
    """Parse `/improvements [approve <id> | reject <id> | rollback <target>]`.
    Returns ("list", None), ("approve"|"reject", id:int), ("rollback", target),
    or None for other commands / malformed args (the dispatcher shows usage)."""
    raw = line.strip().lstrip("/")
    parts = raw.split()
    if not parts or parts[0].lower() != "improvements":
        return None
    if len(parts) == 1:
        return ("list", None)
    sub = parts[1].lower()
    if sub in ("approve", "reject"):
        if len(parts) < 3:
            return ("usage", None)
        try:
            return (sub, int(parts[2]))
        except ValueError:
            return ("usage", None)
    if sub == "rollback":
        if len(parts) < 3:
            return ("usage", None)
        return ("rollback", parts[2])
    return ("usage", None)


def _diff_preview(base: str, variant: str, *, context: int = 2) -> list[str]:
    """A compact unified diff of base→variant (prompts or serialized config)."""
    import difflib

    diff = difflib.unified_diff(
        base.splitlines(), variant.splitlines(),
        fromfile="active", tofile="candidate", lineterm="", n=context,
    )
    lines = list(diff)
    if len(lines) > 24:
        lines = lines[:24] + [f"    … ({len(lines) - 24} more diff lines)"]
    return lines


def _render_improvements_list() -> str:
    """Phase 19e: list open pending promotions with fitness delta + a diff."""
    from ubongo.evolution import promotion, targets
    from ubongo.memory import store as _store

    pending = _store.open_pending_promotions()
    if not pending:
        return "No pending improvements. The loop proposes one when a variant beats the active baseline."
    out = [f"Pending improvements ({len(pending)}):"]
    for p in pending:
        ev = _store.latest_evaluation_for_lineage(p["lineage_id"])
        champ = ev["fitness"] if ev else None
        base = promotion.baseline_fitness(p["target"], p["generation"])
        delta = f"{base:.3f} → {champ:.3f}" if champ is not None else "n/a"
        out.append(f"\n  #{p['id']}  {p['target']}  gen {p['generation']}  "
                   f"strategy={p['strategy']}  fitness {delta}")
        try:
            base_text = targets.resolve_base(p["target"])
        except Exception:
            base_text = ""
        for dl in _diff_preview(base_text, p["variant_text"]):
            out.append(f"    {dl}")
    out.append("\nApprove with /improvements approve <id>, reject <id>, or /improvements rollback <target>.")
    return "\n".join(out)


def _render_improvements_action(action: str, arg) -> str:
    from ubongo.evolution import promotion

    if action == "approve":
        d = promotion.approve(arg)
        if d is None:
            return f"No open promotion #{arg}."
        delta = (f" (fitness {d.baseline_fitness:.3f} → {d.champion_fitness:.3f})"
                 if d.champion_fitness is not None else "")
        return f"Approved #{arg}: {d.target} now uses lineage #{d.lineage_id}{delta}. Live swap in effect."
    if action == "reject":
        d = promotion.reject(arg)
        if d is None:
            return f"No open promotion #{arg}."
        return f"Rejected #{arg}: {d.target} unchanged."
    if action == "rollback":
        ok = promotion.rollback(arg)
        return (f"Rolled back {arg} to its file/default. Live swap reverted."
                if ok else f"No active promotion for {arg}.")
    return "Usage: /improvements [approve <id> | reject <id> | rollback <target>]."


def _parse_recall_command(line: str) -> str | None:
    """Returns the query from `/recall [query]` ("" for no query), or None for
    other commands."""
    raw = line.strip().lstrip("/")
    parts = raw.split(maxsplit=1)
    if parts[0].lower() != "recall":
        return None
    return parts[1].strip() if len(parts) > 1 else ""


def _render_recall(query: str) -> str:
    """Phase 20f: show what recall would surface for the current conversation —
    the recency window, semantic hits (when embeddings are on), and the vault
    graph neighbors of today's daily note. A direct read tool (no master.handle)."""
    from datetime import datetime, timezone

    from ubongo.memory import embeddings, graph, store, vault

    session = store.get_session()
    conv_id = session.current_conversation_id if session else None
    if conv_id is None:
        return "No conversation yet."

    # default query = the latest user message
    if not query:
        recent_user = [m for m in store.last_n_messages(conv_id, 20) if m.role == "user"]
        query = recent_user[-1].content if recent_user else ""

    ctx = store.recall(conv_id, query=query or None)
    lines = [f"Recall for conversation {conv_id}" + (f' — query: "{query}"' if query else "")]

    if ctx.summary_text:
        lines.append(f"\nsummary: {ctx.summary_text[:200]}")

    lines.append(f"\nrecency window (last {len(ctx.messages)}):")
    for m in ctx.messages[-6:]:
        lines.append(f"  {m.role}: {' '.join(m.content.split())[:80]}")

    if not embeddings.enabled():
        lines.append("\nsemantic: (embeddings disabled — recency only)")
    elif not embeddings.vec_available():
        lines.append("\nsemantic: (sqlite-vec unavailable — recency only)")
    elif ctx.semantic_messages:
        lines.append("\nsemantic hits (outside the recency window):")
        for m in ctx.semantic_messages:
            lines.append(f"  #{m.id} {m.role}: {' '.join(m.content.split())[:80]}")
    else:
        lines.append("\nsemantic: (no hits)")

    today = datetime.now(timezone.utc).date().isoformat()
    note = f"{vault._daily_subdir()}/{today}.md"
    nbrs = graph.neighbors(note)
    lines.append(f"\nvault graph — neighbors of {note}: " + (", ".join(nbrs) if nbrs else "(none)"))
    return "\n".join(lines)


_AUDIT_CATEGORIES = ("governance", "evolution", "sync")


def _parse_audit_command(line: str):
    """Parse `/audit [category] [N]`. Returns (category|None, n) or None for
    other commands."""
    raw = line.strip().lstrip("/")
    parts = raw.split()
    if not parts or parts[0].lower() != "audit":
        return None
    category, n = None, 20
    for tok in parts[1:]:
        if tok.lower() in _AUDIT_CATEGORIES:
            category = tok.lower()
        else:
            try:
                n = int(tok)
            except ValueError:
                pass
    return (category, n)


def _render_audit(category, n: int) -> str:
    """Phase 21d: tail the unified audit log, optionally filtered by category."""
    from ubongo.memory import vault

    rows = vault.audit_tail(category, n)
    if not rows:
        return f"No audit entries{f' for {category}' if category else ''}."
    header = f"Audit log (last {len(rows)}{f', {category}' if category else ''}):"
    return header + "\n" + "\n".join(f"  {r[2:]}" for r in rows)


def _parse_conflicts_command(line: str):
    """Parse `/conflicts` (list) or `/conflicts resolve <id> <keep-mine|keep-theirs|merge>`.
    Returns ("list", None, None), ("resolve", id, resolution), ("usage", None, None),
    or None for other commands."""
    raw = line.strip().lstrip("/")
    parts = raw.split()
    if not parts or parts[0].lower() != "conflicts":
        return None
    if len(parts) == 1:
        return ("list", None, None)
    if parts[1].lower() == "resolve" and len(parts) >= 4:
        try:
            cid = int(parts[2])
        except ValueError:
            return ("usage", None, None)
        res = parts[3].lower()
        if res not in ("keep-mine", "keep-theirs", "merge"):
            return ("usage", None, None)
        return ("resolve", cid, res)
    return ("usage", None, None)


def _render_conflicts_list() -> str:
    from ubongo.memory import store

    rows = store.open_vault_conflicts()
    if not rows:
        return "No open vault conflicts."
    lines = [f"Open vault conflicts ({len(rows)}):"]
    for r in rows:
        lines.append(f"  #{r['id']}  {r['path']}  (edited externally at {r['detected_at']})")
    lines.append("Resolve with /conflicts resolve <id> <keep-mine|keep-theirs|merge>.")
    return "\n".join(lines)


def _render_conflicts_resolve(cid: int, resolution: str) -> str:
    from ubongo.memory import store, vault

    conflict = store.get_vault_conflict(cid)
    if conflict is None or conflict["status"] != "open":
        return f"No open conflict #{cid}."
    ok = store.resolve_vault_conflict(cid, resolution)
    if not ok:
        return f"No open conflict #{cid}."
    vault.append_audit_entry("sync", f"resolved conflict #{cid} on {conflict['path']} -> {resolution}")
    note = ""
    if resolution == "keep-mine":
        note = " (note: daily notes are append-only; the system keeps appending and does not snapshot, so the on-disk edit remains)"
    return f"Conflict #{cid} resolved: {resolution}{note}."


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
    traces = store.last_n_workflow_runs(n)
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


def _cmd_optimize(line: str, state: ReplState) -> str | None:
    arg = _parse_optimize_command(line)
    if arg is None or arg == _OPTIMIZE_LIST_SENTINEL:
        return _render_optimize_targets()
    return _render_optimize(arg)


def _cmd_evaluate(line: str, state: ReplState) -> str | None:
    arg = _parse_evaluate_command(line)
    if arg is None or arg == _EVALUATE_LIST_SENTINEL:
        return _render_evaluate_targets()
    return _render_evaluate(arg)


def _cmd_evolution(line: str, state: ReplState) -> str | None:
    sub = _parse_evolution_command(line)
    if sub is None or sub == "status":
        return _render_evolution_status()
    if sub in ("pause", "resume", "off"):
        return _render_evolution_control(sub)
    return f"Unknown subcommand: {sub}. Usage: /evolution status|pause|resume|off."


def _cmd_recall(line: str, state: ReplState) -> str | None:
    q = _parse_recall_command(line)
    return _render_recall(q or "")


def _cmd_audit(line: str, state: ReplState) -> str | None:
    parsed = _parse_audit_command(line)
    cat, n = parsed if parsed else (None, 20)
    return _render_audit(cat, n)


def _cmd_conflicts(line: str, state: ReplState) -> str | None:
    parsed = _parse_conflicts_command(line)
    if parsed is None or parsed[0] == "list":
        return _render_conflicts_list()
    if parsed[0] == "usage":
        return "Usage: /conflicts [resolve <id> <keep-mine|keep-theirs|merge>]."
    return _render_conflicts_resolve(parsed[1], parsed[2])


def _cmd_improvements(line: str, state: ReplState) -> str | None:
    parsed = _parse_improvements_command(line)
    if parsed is None or parsed[0] == "list":
        return _render_improvements_list()
    if parsed[0] == "usage":
        return "Usage: /improvements [approve <id> | reject <id> | rollback <target>]."
    return _render_improvements_action(parsed[0], parsed[1])


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


def _parse_author_command(line: str) -> str | None:
    """`/author <description>` -> the description, or None if missing."""
    raw = line.strip().lstrip("/")
    parts = raw.split(maxsplit=1)
    if not parts or parts[0].lower() != "author":
        return None
    if len(parts) == 1 or not parts[1].strip():
        return None
    return parts[1].strip()


def _render_author(description: str) -> str:
    from ubongo.authoring import manual

    try:
        outcome = manual.author_skill(description)
    except manual.AuthoringError as exc:
        return f"Could not author a skill: {exc}"
    c = outcome.candidate
    kind = "command skill" if c.is_command_skill else "prompt skill"
    lines = [
        f"Drafted candidate #{outcome.candidate_id} '{c.name}' (gen {outcome.generation}, {kind}).",
        f"  risk: {c.risk}   reversibility: {c.reversibility}"
        + (f"   persona: {c.default_persona}" if c.default_persona else ""),
        f"  {c.description}",
    ]
    if c.is_command_skill:
        lines.append(f"  command: {c.command_template.strip()}")
    if outcome.quality is not None:
        lines.append(f"  quality: {outcome.quality:.3f} (estimated)")
    lines.append("  status: quarantined (not discoverable until approved).")
    lines.append("  Review with /skill-candidates.")
    return "\n".join(lines)


def _cmd_author(line: str, state: ReplState) -> str | None:
    description = _parse_author_command(line)
    if not description:
        return f"Usage: /author <capability description>. {_HELP_COMMANDS}"
    return _render_author(description)


_AUTHORING_SUBCOMMANDS = ("status", "pause", "resume", "off")


def _parse_authoring_command(line: str) -> str | None:
    raw = line.strip().lstrip("/")
    parts = raw.split(maxsplit=1)
    if not parts or parts[0].lower() != "authoring":
        return None
    if len(parts) == 1 or not parts[1].strip():
        return "status"
    return parts[1].strip().split()[0].lower()


def _render_authoring_status() -> str:
    from ubongo.config import load_authoring

    status = store.get_authoring_status()
    cap = int((load_authoring() or {}).get("max_calls_per_hour", 20))
    spent = store.authoring_calls_in_last_hour()
    drafts = store.authored_skills(status="draft", limit=100)
    auto = [d for d in drafts if d["source"] == "auto"]
    lines = [
        f"Authoring daemon: {status}  (budget {spent}/{cap} calls in the last hour)",
        f"  pending drafts: {len(drafts)} ({len(auto)} auto-authored) — review with /skill-candidates",
    ]
    runs = store.authoring_runs_recent(5)
    if runs:
        lines.append("  recent cycles:")
        for r in runs:
            lines.append(
                f"    #{r['id']} {r['outcome']:<11} gap={r['gap'] or '-'} "
                f"cand={r['candidate_id'] or '-'} calls={r['calls_spent']}"
            )
    return "\n".join(lines)


def _render_authoring_control(sub: str) -> str:
    if sub == "pause":
        store.set_authoring_status("paused")
        return "Authoring daemon paused."
    if sub == "resume":
        store.set_authoring_status("running")
        return ("Authoring daemon running. It drafts candidates into quarantine on "
                "recurring capability gaps; approval stays manual (/skill-candidates).")
    store.set_authoring_status("off")
    return "Authoring daemon off."


def _cmd_authoring(line: str, state: ReplState) -> str | None:
    sub = _parse_authoring_command(line)
    if sub is None or sub == "status":
        return _render_authoring_status()
    if sub in ("pause", "resume", "off"):
        return _render_authoring_control(sub)
    return f"Unknown subcommand: {sub}. Usage: /authoring status|pause|resume|off."


def _render_skill_candidates_list() -> str:
    rows = store.authored_skills(limit=30)
    if not rows:
        return "No authored skill candidates yet. Draft one with /author <description>."
    lines = ["Authored skill candidates (newest first):"]
    for r in rows:
        cand = r.get("candidate") or {}
        is_cmd = bool((cand.get("command_template") or "").strip())
        quality = r.get("quality")
        q = f" quality={quality:.3f}" if isinstance(quality, (int, float)) else ""
        lines.append(
            f"  #{r['id']} {r['name']:<24} {r['status']:<11} "
            f"gen={r['generation']} {'cmd' if is_cmd else 'prompt'} "
            f"src={r['source']}{q}"
        )
        if r["status"] == "draft":
            lines.extend("      " + dl for dl in _candidate_collision_diff(r))
    lines.append(
        "Approve with /skill-candidates approve <id> (reject <id>, rollback <name>)."
    )
    return "\n".join(lines)


def _candidate_collision_diff(row: dict) -> list[str]:
    """If a draft would overwrite a live skill of the same name, a compact diff
    of the live SKILL.md -> the candidate's, so the reviewer sees the change
    before approving. Empty for a fresh (non-colliding) candidate."""
    from pathlib import Path

    live = skills.skills_dir() / row["name"] / "SKILL.md"
    qpath = row.get("quarantine_path")
    if not live.exists() or not qpath:
        return []
    qmd = Path(qpath) / "SKILL.md"
    if not qmd.exists():
        return []
    try:
        diff = _diff_preview(live.read_text(encoding="utf-8"), qmd.read_text(encoding="utf-8"))
    except OSError:
        return []
    header = f"(would overwrite live '{row['name']}'{'' if diff else '; no textual change'}:)"
    return [header] + diff


def _parse_skill_candidates_command(line: str):
    """Parse `/skill-candidates [approve <id> | reject <id> | rollback <name>]`.
    Returns ("list", None), ("approve"|"reject", id:int), ("rollback", name),
    ("usage", None), or None for other commands."""
    raw = line.strip().lstrip("/")
    parts = raw.split()
    if not parts or parts[0].lower() != "skill-candidates":
        return None
    if len(parts) == 1 or parts[1].lower() == "list":
        return ("list", None)
    sub = parts[1].lower()
    if sub in ("approve", "reject"):
        if len(parts) < 3:
            return ("usage", None)
        try:
            return (sub, int(parts[2]))
        except ValueError:
            return ("usage", None)
    if sub == "rollback":
        if len(parts) < 3:
            return ("usage", None)
        return ("rollback", parts[2])
    return ("usage", None)


def _render_skill_candidates_action(action: str, arg) -> str:
    from ubongo.authoring import promotion

    try:
        if action == "approve":
            r = promotion.approve(arg)
            msg = f"Approved #{r.candidate_id} '{r.name}' — registered and now in /skills."
            if r.backed_up:
                msg += f"\n  Prior version backed up to {r.backup_path}."
            return msg
        if action == "reject":
            r = promotion.reject(arg)
            return f"Rejected #{r.candidate_id} '{r.name}'. Left in quarantine."
        if action == "rollback":
            r = promotion.rollback(arg)
            if r.restored:
                return f"Rolled back '{r.name}' — restored the prior version from {r.backup_path}."
            return f"Rolled back '{r.name}' — unregistered (no prior version to restore)."
    except promotion.PromotionError as exc:
        return f"Cannot do that: {exc}"
    return "Unknown action."


def _cmd_skill_candidates(line: str, state: ReplState) -> str | None:
    parsed = _parse_skill_candidates_command(line)
    if parsed is None or parsed[0] == "list":
        return _render_skill_candidates_list()
    if parsed[0] == "usage":
        return "Usage: /skill-candidates [approve <id> | reject <id> | rollback <name>]."
    return _render_skill_candidates_action(parsed[0], parsed[1])


COMMANDS: dict[str, Command] = {
    "skill":        Command(_cmd_skill, "/skill <name>"),
    "skills":       Command(_cmd_skills, "/skills"),
    "summary":      Command(_cmd_summary, "/summary"),
    "queue":        Command(_cmd_queue, "/queue [N]"),
    "decisions":    Command(_cmd_decisions, "/decisions [N]"),
    "policy":       Command(_cmd_policy, "/policy"),
    "agents":       Command(_cmd_agents, "/agents"),
    "trace":        Command(_cmd_trace, "/trace [N]"),
    "exec":         Command(_cmd_exec, "/exec <cmd>"),
    "mode":         Command(_cmd_mode, "/mode <workflow>"),
    "optimize":     Command(_cmd_optimize, "/optimize <target>"),
    "evaluate":     Command(_cmd_evaluate, "/evaluate <target>"),
    "evolution":    Command(_cmd_evolution, "/evolution <status|pause|resume|off>"),
    "improvements": Command(_cmd_improvements, "/improvements"),
    "author":       Command(_cmd_author, "/author <description>"),
    "authoring":    Command(_cmd_authoring, "/authoring <status|pause|resume|off>"),
    "skill-candidates": Command(_cmd_skill_candidates, "/skill-candidates [approve <id>|reject <id>|rollback <name>]"),
    "recall":       Command(_cmd_recall, "/recall [query]"),
    "audit":        Command(_cmd_audit, "/audit [category]"),
    "conflicts":    Command(_cmd_conflicts, "/conflicts"),
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
        return _repl_loop(persona, auto_mode, pending_skill, pending_workflow)
    finally:
        _evolution_loop.stop()
        _vault_watcher.stop()
        _authoring_loop.stop()


def _repl_loop(persona, auto_mode, pending_skill, pending_workflow) -> int:
    state = ReplState(
        persona=persona, auto_mode=auto_mode,
        pending_skill=pending_skill, pending_workflow=pending_workflow,
    )
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

        response = master.handle(
            stripped, state.persona, state.auto_mode,
            pending_skill=state.pending_skill,
            pending_workflow=state.pending_workflow,
        )
        state.pending_skill = None  # one-shot
        state.pending_workflow = None  # one-shot
        print(response.text)
        queue.flush_delivered(response.delivery_token)
        if state.auto_mode:
            state.persona = response.persona

        # Phase 13f: when Repair gave up, prompt for one-shot retry.
        if response.requires_user_decision:
            choice = _prompt_repair_retry()
            if choice == "y":
                retry_response = master.handle(
                    stripped, state.persona, state.auto_mode,
                    pending_skill=None,
                    pending_workflow=None,
                )
                print(retry_response.text)
                queue.flush_delivered(retry_response.delivery_token)
                if state.auto_mode:
                    state.persona = retry_response.persona
            # On "n" (or anything else), just continue the loop. The user
            # types the next prompt as usual.

        # Phase 15: when governance held the turn for approval, prompt y/n/why.
        if response.approval is not None:
            choice = _prompt_approval(response.approval)
            store.update_governance_decision(response.approval["decision_id"], choice)
            if choice == "y":
                approved_response = master.handle(
                    stripped, state.persona, state.auto_mode,
                    pending_skill=None, pending_workflow=None, approved=True,
                )
                print(approved_response.text)
                queue.flush_delivered(approved_response.delivery_token)
                if state.auto_mode:
                    state.persona = approved_response.persona
            else:
                emit("Aborted; nothing was done.")


if __name__ == "__main__":
    sys.exit(run())
