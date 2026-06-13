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
import time

from ubongo import daemon
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
    from ubongo.memory import index_state
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
        known = index_state.get_vault_hash(rel)
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
            index_state.append_vault_conflict(path=rel, system_hash=known, disk_hash=disk)
            vault.append_audit_entry("sync", f"ingested external edit to {rel}")
            result["conflicts"] += 1
        # The disk hash is now the known state — don't re-ingest the same edit.
        index_state.record_vault_write(rel, disk)
    return result


class VaultWatcher(daemon.DaemonLoop):
    """Daemon thread that calls `scan_once()` on a timer. Lifecycle is the
    DaemonLoop's (sync run style — the default sleep is `time.sleep`);
    injectable sleep/tick for tests; started/stopped by the REPL."""

    name = "vault_watcher"
    log = logger
    thread_name = "vault-watcher"
    started_event = "vault_watcher_started"
    cycle_error_event = "vault_scan_error"

    def __init__(self, *, sleep=None, tick_seconds: float | None = None) -> None:
        super().__init__(sleep=sleep or time.sleep, tick_seconds=tick_seconds)

    def enabled(self) -> bool:
        return enabled()

    def interval(self) -> float:
        if self._tick is not None:
            return self._tick
        return float((load_config().get("vault", {}).get("sync", {}) or {}).get("poll_interval_s", _DEFAULT_POLL_S))

    def run_cycle(self) -> None:
        scan_once()
