"""The daemon lifecycle module — one implementation behind the three loops
(candidate 15).

A **daemon loop** is a background thread the REPL starts and stops around the
interactive session: the GP self-improvement loop, the authoring loop, and the
vault watcher. Before this module each re-implemented the same lifecycle
(thread + stop event + per-cycle exception swallow + injectable sleep/tick),
and two of them carried a byte-identical scheduling gate. This module is that
lifecycle, once; each daemon subclasses it and keeps only its cycle work,
enablement, and status seeding.

Two run styles are supported, chosen by the injected sleep: a coroutine
function (the GP/authoring default, `asyncio.sleep`) runs the loop inside
`asyncio.run` on the thread; a plain callable (the watcher's default,
`time.sleep`) runs a synchronous loop. Tests inject fake sleeps of either
kind, exactly as before.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading

_fallback_logger = logging.getLogger("ubongo.daemon")


def should_cycle(*, status: str, remaining: int,
                 seconds_since_last: float | None, cron) -> bool:
    """Pure scheduling gate shared by the budgeted daemons:

    - status must be "running";
    - the rolling-hour budget must have room (remaining > 0);
    - if `cron` (int seconds) is set, at least that many seconds must have
      elapsed since the last cycle ended.

    (Was duplicated byte-for-byte in evolution/loop.py and authoring/loop.py.)
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


class DaemonLoop:
    """Daemon-thread scheduler. Subclasses provide the parts that genuinely
    differ — `name`, `log`, the `*_event` names, `enabled()`, `seed()`,
    `interval()`, `start_extra()`, and `run_cycle()` — and inherit the
    lifecycle: `start() -> bool`, `stop(timeout)`, the per-cycle exception
    swallow, and the crash guard around the whole thread."""

    name = "daemon"
    log: logging.Logger = _fallback_logger
    thread_name: str | None = None        # default: "<name>-loop"
    started_event: str | None = None      # default: "<name>_loop_started"
    crashed_event: str | None = None      # default: "<name>_loop_crashed"
    cycle_error_event: str | None = None  # default: "<name>_cycle_error"

    def __init__(self, *, sleep=None, tick_seconds: float | None = None) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sleep = sleep
        self._tick = tick_seconds

    # ---- per-daemon hooks -------------------------------------------------

    def enabled(self) -> bool:
        """Config/env gate checked by start(); False means never spawn."""
        return True

    def seed(self) -> None:
        """First-launch setup (e.g. seed the persisted status row)."""

    def interval(self) -> float:
        """Seconds between cycles; resolved once when the loop thread starts."""
        return self._tick if self._tick is not None else 5.0

    def start_extra(self) -> dict:
        """Extra fields for the started log line (e.g. the persisted status)."""
        return {}

    def run_cycle(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    # ---- the lifecycle (the part that was duplicated) ---------------------

    def start(self) -> bool:
        if not self.enabled():
            return False
        self.seed()
        self._thread = threading.Thread(
            target=self._thread_main,
            name=self.thread_name or f"{self.name}-loop",
            daemon=True,
        )
        self._thread.start()
        self.log.info(self.started_event or f"{self.name}_loop_started",
                      extra=self.start_extra())
        return True

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _thread_main(self) -> None:
        try:
            if inspect.iscoroutinefunction(self._sleep):
                asyncio.run(self._run_async())
            else:
                self._run_sync()
        except Exception:  # daemon thread: never take the process down silently
            self.log.exception(self.crashed_event or f"{self.name}_loop_crashed")

    def _cycle_guarded(self) -> None:
        try:
            self.run_cycle()
        except Exception:
            self.log.exception(self.cycle_error_event or f"{self.name}_cycle_error")

    async def _run_async(self) -> None:
        interval = self.interval()
        while not self._stop.is_set():
            self._cycle_guarded()
            await self._sleep(interval)

    def _run_sync(self) -> None:
        interval = self.interval()
        sleep = self._sleep
        while not self._stop.is_set():
            self._cycle_guarded()
            sleep(interval)
