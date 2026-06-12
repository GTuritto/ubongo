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
from dataclasses import dataclass

from ubongo.authoring import fitness, gaps, manual, sandbox
from ubongo.authoring.candidate import SkillCandidate
from ubongo import daemon
from ubongo.config import load_authoring
from ubongo.evaluation import CallBudget
from ubongo.memory import authoring_state
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
        pending = authoring_state.auto_drafts_unevaluated(limit=1)
        if pending:
            row = pending[0]
            candidate = SkillCandidate.from_dict(row["candidate"])
            run_id = authoring_state.start_authoring_run(gap=None)
            metrics = sandbox.evaluate_candidate(candidate, samples_per_eval=spe)
            quality = fitness.score_candidate(metrics) if metrics else None
            if quality is not None:
                authoring_state.update_authored_skill(row["id"], quality=quality)
            calls = spe * CallBudget.CALLS_PER_SAMPLE
            authoring_state.finish_authoring_run(run_id, calls_spent=calls, outcome="reevaluated",
                                       candidate_id=row["id"])
            logger.info("authoring_recovered", extra={"id": row["id"], "quality": quality})
            return AuthoringCycleResult("reevaluated", candidate_id=row["id"],
                                        quality=quality, calls_spent=calls)

    # --- infer a gap and draft for it ---
    gap = gaps.next_gap()
    if gap is None:
        return AuthoringCycleResult("idle", note="no recurring capability gaps")

    run_id = authoring_state.start_authoring_run(gap=gap.intent)
    try:
        outcome = manual.author_skill(gap.description, source="auto")
    except manual.AuthoringError as exc:
        authoring_state.finish_authoring_run(run_id, calls_spent=1, outcome="aborted")
        logger.info("authoring_cycle_aborted", extra={"gap": gap.intent, "cause": str(exc)})
        return AuthoringCycleResult("aborted", gap=gap.intent, note=str(exc))

    scored = outcome.quality is not None
    calls = 1 + (spe * CallBudget.CALLS_PER_SAMPLE if scored else 0)
    authoring_state.finish_authoring_run(run_id, calls_spent=calls,
                               outcome="evaluated" if scored else "drafted",
                               candidate_id=outcome.candidate_id)
    logger.info("authoring_cycle", extra={"gap": gap.intent, "id": outcome.candidate_id,
                                          "quality": outcome.quality})
    return AuthoringCycleResult("drafted", candidate_id=outcome.candidate_id,
                                gap=gap.intent, quality=outcome.quality, calls_spent=calls)


# Shared gate (candidate 15); the alias keeps the import/test surface.
_should_cycle = daemon.should_cycle


class AuthoringLoop(daemon.DaemonLoop):
    """Daemon-thread scheduler. Boots paused; runs `run_one_cycle` when the
    shared gate allows. Lifecycle is the DaemonLoop's; sleep + tick stay
    injectable for tests."""

    name = "authoring"
    log = logger

    def __init__(self, *, sleep=None, tick_seconds: float = _DEFAULT_TICK_SECONDS) -> None:
        super().__init__(sleep=sleep or asyncio.sleep, tick_seconds=tick_seconds)

    def enabled(self) -> bool:
        if os.environ.get("UBONGO_DISABLE_AUTHORING") == "1":
            return False
        return bool(load_authoring().get("enabled", False))

    def seed(self) -> None:
        if authoring_state.get_authoring_status() not in ("running", "paused", "off"):
            authoring_state.set_authoring_status("paused")

    def start_extra(self) -> dict:
        return {"status": authoring_state.get_authoring_status()}

    def run_cycle(self) -> None:
        self._maybe_run_cycle()

    def _maybe_run_cycle(self) -> None:
        cfg = load_authoring()
        cap = int(cfg.get("max_calls_per_hour", _DEFAULT_MAX_CALLS))
        remaining = cap - authoring_state.authoring_calls_in_last_hour()
        if not _should_cycle(
            status=authoring_state.get_authoring_status(), remaining=remaining,
            seconds_since_last=authoring_state.authoring_seconds_since_last_cycle(), cron=cfg.get("cron"),
        ):
            return
        run_one_cycle(budget=CallBudget(remaining))
