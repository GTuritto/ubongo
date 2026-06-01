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
import logging
import threading
from dataclasses import dataclass

from ubongo import events
from ubongo.config import load_evolution
from ubongo.evolution import fitness, generator, lineage, sandbox, selection
from ubongo.evolution.sandbox import CallBudget
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
        store.append_evaluation(
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

    latest_gen = store.max_lineage_generation(target)

    # --- recovery: re-evaluate an incomplete latest generation ---
    if latest_gen > 0:
        variant_rows = store.lineage_for_target(target, generation=latest_gen)
        eval_rows = store.evaluations_for_target(target, generation=latest_gen)
        if variant_rows and len(eval_rows) < len(variant_rows):
            run_id = store.start_evolution_run(target=target, generation=latest_gen)
            result = sandbox.evaluate_target(variant_rows, target, budget=budget)
            persist_cohort_evaluations(result)
            store.finish_evolution_run(run_id, calls_spent=budget.spent, outcome=_outcome(result))
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
    run_id = store.start_evolution_run(target=target, generation=new_gen)

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
        store.finish_evolution_run(run_id, calls_spent=budget.spent, outcome="aborted")
        return CycleResult(
            target=target, generation=new_gen, action="aborted",
            calls_spent=budget.spent, note="generator produced no variants",
        )

    lineage.record_variants(target, variants)  # next_generation resolves to new_gen
    variant_rows = store.lineage_for_target(target, generation=new_gen)
    result = sandbox.evaluate_target(variant_rows, target, budget=budget)
    persist_cohort_evaluations(result)
    store.finish_evolution_run(run_id, calls_spent=budget.spent, outcome=_outcome(result))
    events.dispatch("evolution_generation", {
        "target": target, "generation": new_gen, "action": "generated",
        "evaluated": result.evaluated, "skipped": result.skipped,
        "seeded_from": parent["lineage_id"] if parent else None,
    })
    return CycleResult(
        target=target, generation=new_gen, action="generated",
        calls_spent=budget.spent, evaluated=result.evaluated, skipped=result.skipped,
    )


def _should_cycle(*, status: str, remaining: int, seconds_since_last: float | None, cron) -> bool:
    """Pure gate: may the scheduler start a cycle right now?

    - status must be "running";
    - the rolling-hour budget must have room (remaining > 0);
    - if `cron` (int seconds) is set, at least that many seconds must have
      elapsed since the last cycle ended.
    """
    if status != "running":
        return False
    if remaining <= 0:
        return False
    if cron is not None and seconds_since_last is not None:
        try:
            if seconds_since_last < float(cron):
                return False
        except (TypeError, ValueError):
            pass
    return True


class EvolutionLoop:
    """Thin scheduler: a daemon thread running an asyncio loop that drives
    `run_one_cycle` when `_should_cycle` allows. Sleep + tick injectable so
    tests never wait real time."""

    def __init__(self, *, sleep=None, tick_seconds: float = _DEFAULT_TICK_SECONDS) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sleep = sleep or asyncio.sleep
        self._tick = tick_seconds

    def start(self) -> bool:
        """Start the daemon thread if `evolution.enabled`. The loop comes up in
        whatever status is persisted (default 'paused'), so it never auto-spends
        on launch. Returns True if the thread was started."""
        if not load_evolution().get("enabled", False):
            return False
        # Seed the control row to 'paused' on first ever launch.
        if store.get_evolution_status() not in ("running", "paused", "off"):
            store.set_evolution_status("paused")
        self._thread = threading.Thread(target=self._thread_main, name="evolution-loop", daemon=True)
        self._thread.start()
        logger.info("evolution_loop_started", extra={"status": store.get_evolution_status()})
        return True

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception:  # daemon thread: never let it take down the process silently
            logger.exception("evolution_loop_crashed")

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._maybe_run_cycle()
            except Exception:
                logger.exception("evolution_cycle_error")
            await self._sleep(self._tick)

    def _maybe_run_cycle(self) -> None:
        evo = load_evolution()
        status = store.get_evolution_status()
        cap = int(evo.get("max_calls_per_hour", 30))
        remaining = cap - store.calls_in_last_hour()
        cron = evo.get("cron")
        if not _should_cycle(
            status=status, remaining=remaining,
            seconds_since_last=store.seconds_since_last_cycle(), cron=cron,
        ):
            return
        run_one_cycle(budget=CallBudget(remaining))
