"""The standing-jobs daemon (v0.5 phase 06): a fourth DaemonLoop.

Mirrors `EvolutionLoop` / `AuthoringLoop`: a thin scheduler over the shared
`DaemonLoop` lifecycle. Boots PAUSED (persisted status), so the system never
speaks unprompted until `/jobs resume`. Each tick consults the shared gate
(status / rolling-hour throttle / cron pacing) and runs one cycle off-thread.
`UBONGO_DISABLE_JOBS=1` keeps the thread from starting (the suite stays
daemon-free).
"""

from __future__ import annotations

import asyncio
import logging
import os

from ubongo import daemon
from ubongo.config import load_jobs
from ubongo.jobs import runner
from ubongo.memory import jobs_state

logger = logging.getLogger("ubongo.jobs.loop")

_DEFAULT_TICK_SECONDS = 5.0
_DEFAULT_MAX_RUNS_PER_HOUR = 20


class StandingJobsLoop(daemon.DaemonLoop):
    name = "jobs"
    log = logger

    def __init__(self, *, sleep=None, tick_seconds: float = _DEFAULT_TICK_SECONDS) -> None:
        super().__init__(sleep=sleep or asyncio.sleep, tick_seconds=tick_seconds)

    def enabled(self) -> bool:
        if os.environ.get("UBONGO_DISABLE_JOBS") == "1":
            return False
        return bool(load_jobs().get("enabled", False))

    def seed(self) -> None:
        # Boot paused on first ever launch: never speak unprompted on launch.
        if jobs_state.get_jobs_status() not in ("running", "paused", "off"):
            jobs_state.set_jobs_status("paused")

    def start_extra(self) -> dict:
        return {"status": jobs_state.get_jobs_status()}

    def run_cycle(self) -> None:
        cfg = load_jobs()
        status = jobs_state.get_jobs_status()
        cap = int(cfg.get("max_runs_per_hour", _DEFAULT_MAX_RUNS_PER_HOUR))
        remaining = cap - jobs_state.runs_in_last_hour()
        cron = cfg.get("cron")
        if not daemon.should_cycle(
            status=status, remaining=remaining,
            seconds_since_last=jobs_state.seconds_since_last_cycle(), cron=cron,
        ):
            return
        runner.run_one_cycle()
