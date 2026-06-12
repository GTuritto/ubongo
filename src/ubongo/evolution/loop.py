"""Autonomous GP loop (Phase 18a/18b).

Three concerns, deliberately separated:

- `run_one_cycle(*, budget)` — PURE and synchronous (no sleeps). Picks the
  stalest target, generates one generation (seeded from the previous one's
  champion survivor) or re-evaluates an interrupted one, evaluates it under the
  shared budget, records an `evolution_runs` row, emits `evolution_generation`.
  This is what the tests drive directly.
- `_should_cycle(...)` — the pure gate (status / throttle / cron interval).
- `EvolutionLoop` — the thin scheduler: a daemon thread running an asyncio loop
  that, each tick, consults `_should_cycle` and runs `run_one_cycle` off-thread.
  Sleep + tick are injectable so tests never wait real time.

Progress lives entirely in SQLite, so killing the REPL mid-generation and
restarting resumes from the last completed generation (an unevaluated latest
generation is re-evaluated rather than regenerated).
"""

from __future__ import annotations

import asyncio
import os
import logging
from dataclasses import dataclass

from ubongo import events
from ubongo import daemon
from ubongo.config import load_evolution
from ubongo.evolution import fitness, generator, lineage, sandbox, selection
from ubongo.evolution.sandbox import CallBudget
from ubongo.memory import evolution_state
from ubongo.memory import store

logger = logging.getLogger("ubongo.evolution.loop")

_DEFAULT_POPULATION = 8
_DEFAULT_SURVIVORS = 3
_DEFAULT_TICK_SECONDS = 5.0


@dataclass(frozen=True)
class CycleResult:
    """Outcome of one GP cycle."""

    target: str | None
    generation: int | None
    action: str  # "generated" | "reevaluated" | "aborted" | "idle"
    calls_spent: int = 0
    evaluated: int = 0
    skipped: int = 0
    note: str = ""


def persist_cohort_evaluations(result: "sandbox.TargetEvaluation") -> list:
    """Rank a TargetEvaluation's cohort and write one evolution_evaluations row
    per variant. Shared by the loop and the `/evaluate` REPL command so the
    persist orchestration lives in one place (the sandbox stays side-effect
    free). Returns the ranked (metrics, fitness) pairs, best first."""
    ranked = fitness.rank_cohort(result.cohort)
    for metrics, fit in ranked:
        evolution_state.append_evaluation(
            lineage_id=metrics.lineage_id,
            sample_set=result.sample_set_version,
            success_rate=metrics.success_rate,
            cost=metrics.cost,
            latency_ms=metrics.latency_ms,
            hallucination_rate=metrics.hallucination_rate,
            user_correction_rate=metrics.user_correction_rate,
            fitness=fit,
        )
    return ranked


def _outcome(result: "sandbox.TargetEvaluation") -> str:
    if result.evaluated == 0:
        return "aborted"
    return "completed" if result.skipped == 0 else "partial"


def run_one_cycle(
    *,
    budget: CallBudget,
    population_size: int | None = None,
    survivors_k: int | None = None,
) -> CycleResult:
    """Run one GP cycle against the stalest target under `budget`. Pure +
    synchronous (no sleeps); the scheduler calls it off-thread.

    Crash recovery: if the target's latest generation has variants but is not
    fully evaluated, re-evaluate it instead of generating a new one. Otherwise
    generate generation N+1, seeded from generation N's champion survivor
    (cross-generation lineage), and evaluate it.
    """
    evo = load_evolution()
    population_size = population_size or int(evo.get("population_size", _DEFAULT_POPULATION))
    survivors_k = survivors_k or int(evo.get("survivors", _DEFAULT_SURVIVORS))

    target = selection.next_target()
    if target is None:
        return CycleResult(target=None, generation=None, action="idle", note="no evolvable targets")

    latest_gen = evolution_state.max_lineage_generation(target)

    # --- recovery: re-evaluate an incomplete latest generation ---
    if latest_gen > 0:
        variant_rows = evolution_state.lineage_for_target(target, generation=latest_gen)
        eval_rows = evolution_state.evaluations_for_target(target, generation=latest_gen)
        if variant_rows and len(eval_rows) < len(variant_rows):
            run_id = evolution_state.start_evolution_run(target=target, generation=latest_gen)
            result = sandbox.evaluate_target(variant_rows, target, budget=budget)
            persist_cohort_evaluations(result)
            evolution_state.finish_evolution_run(run_id, calls_spent=budget.spent, outcome=_outcome(result))
            if result.evaluated > 0:
                from ubongo.evolution import promotion
                promotion.propose_if_better(target, latest_gen)
            events.dispatch("evolution_generation", {
                "target": target, "generation": latest_gen, "action": "reevaluated",
                "evaluated": result.evaluated, "skipped": result.skipped,
            })
            return CycleResult(
                target=target, generation=latest_gen, action="reevaluated",
                calls_spent=budget.spent, evaluated=result.evaluated, skipped=result.skipped,
            )

    # --- generate the next generation ---
    new_gen = latest_gen + 1
    run_id = evolution_state.start_evolution_run(target=target, generation=new_gen)

    parent = None
    if latest_gen > 0:
        survs = selection.survivors(target, latest_gen, survivors_k)
        if survs:
            parent = survs[0]  # champion seeds the next generation

    if parent is not None:
        variants = generator.generate(
            target, population_size, budget=budget,
            parent_text=parent["variant_text"], parent_id=parent["lineage_id"],
        )
    else:
        variants = generator.generate(target, population_size, budget=budget)

    if not variants:
        evolution_state.finish_evolution_run(run_id, calls_spent=budget.spent, outcome="aborted")
        return CycleResult(
            target=target, generation=new_gen, action="aborted",
            calls_spent=budget.spent, note="generator produced no variants",
        )

    lineage.record_variants(target, variants)  # next_generation resolves to new_gen
    variant_rows = evolution_state.lineage_for_target(target, generation=new_gen)
    result = sandbox.evaluate_target(variant_rows, target, budget=budget)
    persist_cohort_evaluations(result)
    evolution_state.finish_evolution_run(run_id, calls_spent=budget.spent, outcome=_outcome(result))
    # Phase 19: propose a promotion when this generation's champion beats the
    # active baseline by the margin. The user approves via /improvements.
    if result.evaluated > 0:
        from ubongo.evolution import promotion
        promotion.propose_if_better(target, new_gen)
    events.dispatch("evolution_generation", {
        "target": target, "generation": new_gen, "action": "generated",
        "evaluated": result.evaluated, "skipped": result.skipped,
        "seeded_from": parent["lineage_id"] if parent else None,
    })
    return CycleResult(
        target=target, generation=new_gen, action="generated",
        calls_spent=budget.spent, evaluated=result.evaluated, skipped=result.skipped,
    )


# The pure scheduling gate lives in the shared daemon module (candidate 15);
# this alias keeps the long-standing import/test surface.
_should_cycle = daemon.should_cycle


class EvolutionLoop(daemon.DaemonLoop):
    """Thin scheduler: a daemon thread that drives `run_one_cycle` when the
    shared gate allows. Lifecycle (thread, stop, per-cycle swallow) is the
    DaemonLoop's; sleep + tick stay injectable so tests never wait real time.

    Candidate 15 also adds the UBONGO_DISABLE_EVOLUTION off-switch, for parity
    with the authoring loop and the vault watcher."""

    name = "evolution"
    log = logger

    def __init__(self, *, sleep=None, tick_seconds: float = _DEFAULT_TICK_SECONDS) -> None:
        super().__init__(sleep=sleep or asyncio.sleep, tick_seconds=tick_seconds)

    def enabled(self) -> bool:
        if os.environ.get("UBONGO_DISABLE_EVOLUTION") == "1":
            return False
        return bool(load_evolution().get("enabled", False))

    def seed(self) -> None:
        # Seed the control row to 'paused' on first ever launch, so the loop
        # never auto-spends on launch.
        if evolution_state.get_evolution_status() not in ("running", "paused", "off"):
            evolution_state.set_evolution_status("paused")

    def start_extra(self) -> dict:
        return {"status": evolution_state.get_evolution_status()}

    def run_cycle(self) -> None:
        self._maybe_run_cycle()

    def _maybe_run_cycle(self) -> None:
        evo = load_evolution()
        status = evolution_state.get_evolution_status()
        cap = int(evo.get("max_calls_per_hour", 30))
        remaining = cap - evolution_state.calls_in_last_hour()
        cron = evo.get("cron")
        if not _should_cycle(
            status=status, remaining=remaining,
            seconds_since_last=evolution_state.seconds_since_last_cycle(), cron=cron,
        ):
            return
        run_one_cycle(budget=CallBudget(remaining))
