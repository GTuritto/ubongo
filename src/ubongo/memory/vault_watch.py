"""Bidirectional vault sync — a polling watcher (Phase 21a/21b).

A no-dependency daemon thread (mirroring `evolution.loop.EvolutionLoop`) that
scans the vault's daily notes every few seconds and ingests **external** edits
the user makes in Obsidian. It tells its own writes from user edits via
`vault_state` (the hash the system last wrote): disk hash matches → system write,
skip; disk hash differs → external edit → re-embed into `vec_vault`, and (when
the system manages that note) queue a conflict for `/conflicts`.

`scan_once()` is pure and synchronous (the unit-test entry point); the thread
just calls it on a timer. Off by default — `vault.sync.enabled` (or the
`UBONGO_DISABLE_VAULT_WATCH` off-switch) gates it, like the GP loop.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from ubongo.config import load_config

logger = logging.getLogger("ubongo.memory.vault_watch")

_DEFAULT_POLL_S = 5.0


def enabled() -> bool:
    if os.environ.get("UBONGO_DISABLE_VAULT_WATCH"):
        return False
    return bool((load_config().get("vault", {}).get("sync", {}) or {}).get("enabled", False))


def scan_once() -> dict:
    """One scan of the vault daily notes. Ingests external edits, queues
    conflicts. Returns {ingested, conflicts}. Best-effort; never raises."""
    from ubongo.memory import embeddings, store, vault

    result = {"ingested": 0, "conflicts": 0}
    root = vault._vault_root()
    daily = root / vault._daily_subdir()
    if not daily.exists():
        return result

    for f in sorted(daily.glob("*.md")):
        if f.name.startswith("."):
            continue
        rel = vault.vault_relpath(f)
        disk = vault.file_hash(f)
        if not disk:
            continue
        known = store.get_vault_hash(rel)
        if known == disk:
            continue  # the system's own write (or unchanged) — no echo
        # External edit (or a note the system has never written).
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        embeddings.index_vault(rel, text)
        result["ingested"] += 1
        if known is not None:
            # The system manages this note and it changed externally -> a
            # collision the user may want to resolve. For append-only daily
            # notes the practical resolution is usually "coexist".
            store.append_vault_conflict(path=rel, system_hash=known, disk_hash=disk)
            vault.append_audit_entry("sync", f"ingested external edit to {rel}")
            result["conflicts"] += 1
        # The disk hash is now the known state — don't re-ingest the same edit.
        store.record_vault_write(rel, disk)
    return result


class VaultWatcher:
    """Daemon thread that calls `scan_once()` on a timer. Injectable sleep/tick
    for tests; started/stopped by the REPL."""

    def __init__(self, *, sleep=None, tick_seconds: float | None = None) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sleep = sleep or time.sleep
        self._tick = tick_seconds

    def _interval(self) -> float:
        if self._tick is not None:
            return self._tick
        return float((load_config().get("vault", {}).get("sync", {}) or {}).get("poll_interval_s", _DEFAULT_POLL_S))

    def start(self) -> bool:
        if not enabled():
            return False
        self._thread = threading.Thread(target=self._run, name="vault-watcher", daemon=True)
        self._thread.start()
        logger.info("vault_watcher_started")
        return True

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        interval = self._interval()
        while not self._stop.is_set():
            try:
                scan_once()
            except Exception:
                logger.exception("vault_scan_error")
            self._sleep(interval)
