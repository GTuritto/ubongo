"""The evolution command pack (candidate 18).

Slash-command handlers, parsers, and renderers for the evolution subsystem,
moved out of repl.py so a evolution change edits this package. Registered via
the COMMANDS fragment below, which repl.py merges into its registry; handler
contract per ubongo.commands (pure: line + ReplState -> text). The help banner
is derived from the merged registry, so packs resolve it late via _help().
"""

from __future__ import annotations

import logging

from ubongo.evaluation import diff_preview
from ubongo.commands import Command, ReplState
from ubongo.commands import format_time as _format_time  # noqa: F401
from ubongo.commands import parse_int_arg as _parse_int_arg  # noqa: F401
from ubongo.memory import store

logger = logging.getLogger("ubongo.evolution.commands")


def _help() -> str:
    from ubongo import repl
    return repl._HELP_COMMANDS


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
    from ubongo.memory import evolution_state as _store

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
    from ubongo.memory import evolution_state as _store

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
    from ubongo.memory import evolution_state as _store

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

def _render_improvements_list() -> str:
    """Phase 19e: list open pending promotions with fitness delta + a diff."""
    from ubongo.evolution import promotion, targets
    from ubongo.memory import evolution_state as _store

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
        for dl in diff_preview(base_text, p["variant_text"]):
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

def _cmd_improvements(line: str, state: ReplState) -> str | None:
    parsed = _parse_improvements_command(line)
    if parsed is None or parsed[0] == "list":
        return _render_improvements_list()
    if parsed[0] == "usage":
        return "Usage: /improvements [approve <id> | reject <id> | rollback <target>]."
    return _render_improvements_action(parsed[0], parsed[1])

# The registry fragment repl.py merges (order preserved by the assembler).
COMMANDS: dict[str, Command] = {
    "optimize": Command(_cmd_optimize, "/optimize <target>"),
    "evaluate": Command(_cmd_evaluate, "/evaluate <target>"),
    "evolution": Command(_cmd_evolution, "/evolution <status|pause|resume|off>"),
    "improvements": Command(_cmd_improvements, "/improvements"),
}
