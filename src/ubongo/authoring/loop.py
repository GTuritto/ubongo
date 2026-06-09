"""The autonomous authoring daemon (Phase 4b).

Mirrors `evolution.loop` deliberately: a daemon thread runs an asyncio loop that,
each tick, consults a pure gate (`_should_cycle`) and runs one cycle off-thread.
The loop boots PAUSED (persisted status) and is throttled by a rolling-hour call
budget, so it never auto-spends on launch.

A cycle DRAFTS — it never approves. The candidate it produces is quarantined,
exactly like a manual `/author`; the human approval boundary
(`/skill-candidates approve`) is unchanged. The daemon's only job is to surface
capability gaps as reviewable drafts.

`run_one_cycle` is pure + synchronous (no sleeps) so tests drive it directly.
Progress lives in `authoring_runs`, so a crash mid-cycle is recovered on restart:
an auto draft left unevaluated is re-evaluated rather than re-drafted.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass

from ubongo.authoring import fitness, gaps, manual, sandbox
from ubongo.authoring.candidate import SkillCandidate
from ubongo.config import load_authoring
from ubongo.evolution.sandbox import CallBudget
from ubongo.memory import store

logger = logging.getLogger("ubongo.authoring.loop")

_DEFAULT_TICK_SECONDS = 5.0
_DEFAULT_MAX_CALLS = 20
_DEFAULT_SAMPLES = 3


@dataclass(frozen=True)
class AuthoringCycleResult:
    action: str  # "drafted" | "reevaluated" | "idle" | "aborted"
    candidate_id: int | None = None
    gap: str | None = None
    quality: float | None = None
    calls_spent: int = 0
    note: str = ""


def _samples_per_eval() -> int:
    return int((load_authoring() or {}).get("samples_per_eval", _DEFAULT_SAMPLES))


def run_one_cycle(*, budget: CallBudget | None = None) -> AuthoringCycleResult:
    """Run one authoring cycle: recover an unevaluated draft if any, else infer a
    gap and draft+evaluate one candidate into quarantine. Pure + synchronous."""
    spe = _samples_per_eval()
    eval_enabled = not sandbox._eval_disabled()

    # --- recovery: finish an auto draft that was persisted but not scored ---
    if eval_enabled:
        pending = store.auto_drafts_unevaluated(limit=1)
        if pending:
            row = pending[0]
            candidate = SkillCandidate.from_dict(row["candidate"])
            run_id = store.start_authoring_run(gap=None)
            metrics = sandbox.evaluate_candidate(candidate, samples_per_eval=spe)
            quality = fitness.score_candidate(metrics) if metrics else None
            if quality is not None:
                store.update_authored_skill(row["id"], quality=quality)
            calls = spe * CallBudget.CALLS_PER_SAMPLE
            store.finish_authoring_run(run_id, calls_spent=calls, outcome="reevaluated",
                                       candidate_id=row["id"])
            logger.info("authoring_recovered", extra={"id": row["id"], "quality": quality})
            return AuthoringCycleResult("reevaluated", candidate_id=row["id"],
                                        quality=quality, calls_spent=calls)

    # --- infer a gap and draft for it ---
    gap = gaps.next_gap()
    if gap is None:
        return AuthoringCycleResult("idle", note="no recurring capability gaps")

    run_id = store.start_authoring_run(gap=gap.intent)
    try:
        outcome = manual.author_skill(gap.description, source="auto")
    except manual.AuthoringError as exc:
        store.finish_authoring_run(run_id, calls_spent=1, outcome="aborted")
        logger.info("authoring_cycle_aborted", extra={"gap": gap.intent, "cause": str(exc)})
        return AuthoringCycleResult("aborted", gap=gap.intent, note=str(exc))

    scored = outcome.quality is not None
    calls = 1 + (spe * CallBudget.CALLS_PER_SAMPLE if scored else 0)
    store.finish_authoring_run(run_id, calls_spent=calls,
                               outcome="evaluated" if scored else "drafted",
                               candidate_id=outcome.candidate_id)
    logger.info("authoring_cycle", extra={"gap": gap.intent, "id": outcome.candidate_id,
                                          "quality": outcome.quality})
    return AuthoringCycleResult("drafted", candidate_id=outcome.candidate_id,
                                gap=gap.intent, quality=outcome.quality, calls_spent=calls)


def _should_cycle(*, status: str, remaining: int, seconds_since_last: float | None, cron) -> bool:
    """Pure gate: status must be 'running', the rolling-hour budget must have
    room, and any `authoring.cron` interval must have elapsed."""
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


class AuthoringLoop:
    """Daemon-thread scheduler. Boots paused; runs `run_one_cycle` when
    `_should_cycle` allows. Sleep + tick injectable for tests."""

    def __init__(self, *, sleep=None, tick_seconds: float = _DEFAULT_TICK_SECONDS) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sleep = sleep or asyncio.sleep
        self._tick = tick_seconds

    def start(self) -> bool:
        """Start the daemon if authoring.enabled and not disabled by the test
        off-switch. Comes up in the persisted status (default 'paused')."""
        if os.environ.get("UBONGO_DISABLE_AUTHORING") == "1":
            return False
        if not load_authoring().get("enabled", False):
            return False
        if store.get_authoring_status() not in ("running", "paused", "off"):
            store.set_authoring_status("paused")
        self._thread = threading.Thread(target=self._thread_main, name="authoring-loop", daemon=True)
        self._thread.start()
        logger.info("authoring_loop_started", extra={"status": store.get_authoring_status()})
        return True

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception:
            logger.exception("authoring_loop_crashed")

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._maybe_run_cycle()
            except Exception:
                logger.exception("authoring_cycle_error")
            await self._sleep(self._tick)

    def _maybe_run_cycle(self) -> None:
        cfg = load_authoring()
        cap = int(cfg.get("max_calls_per_hour", _DEFAULT_MAX_CALLS))
        remaining = cap - store.authoring_calls_in_last_hour()
        if not _should_cycle(
            status=store.get_authoring_status(), remaining=remaining,
            seconds_since_last=store.authoring_seconds_since_last_cycle(), cron=cfg.get("cron"),
        ):
            return
        run_one_cycle(budget=CallBudget(remaining))
