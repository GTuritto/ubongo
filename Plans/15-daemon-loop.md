# Phase 15 — One daemon lifecycle module behind the three loops

Branch: `improve/15-daemon-loop`. Lifted from
[Plans/14-19-architecture-deepening-roadmap.md](14-19-architecture-deepening-roadmap.md)
("Proceed with the Plan", 2026-06-11). Strength: **Strong**. Behavior-neutral:
**no VERSION bump**.

## Problem

Three background daemons re-implement the same lifecycle. `_should_cycle` was
byte-identical in `evolution/loop.py:170-188` and `authoring/loop.py:101-114`;
the lifecycle classes (`__init__/start/stop/_thread_main/_run/_maybe_run_cycle`)
differed only by name substitution; `VaultWatcher` (`vault_watch.py:75-110`)
was the same shape minus the gate, in a sync variant. Live drift symptom:
authoring and the watcher have env off-switches; evolution had none.

## Solution

`src/ubongo/daemon.py`:

- `should_cycle(...)` — the shared pure scheduling gate (status / rolling-hour
  budget / cron). The loops keep `_should_cycle = daemon.should_cycle` aliases
  so the long-standing import/test surface is untouched.
- `DaemonLoop` — the lifecycle, once: `start() -> bool` (enabled-gate, seed,
  spawn, started-log with per-daemon extras), `stop(timeout)`, the per-cycle
  exception swallow, the whole-thread crash guard, and **both run styles**
  chosen by the injected sleep (coroutine function → asyncio loop on the
  thread, exactly the GP/authoring shape; plain callable → sync loop, the
  watcher's shape). Subclass hooks: `name`, `log`, `*_event` names,
  `enabled()`, `seed()`, `interval()`, `start_extra()`, `run_cycle()`.

The three daemons become subclasses holding only what differs: their cycle
work, enablement (config + env switch), status seeding, and the watcher's
config-driven interval. All log event names, logger names, thread names, and
constructor signatures are preserved exactly. **15.3**: `EvolutionLoop` gains
the missing `UBONGO_DISABLE_EVOLUTION` off-switch (parity; additive).

## Candidate 16 (repair read seam) — assessed and DROPPED as the ride-along

The roadmap defaulted 16 into this phase. Verified against the tree:
`store.repair_runs_for_workflow` has ~10 dict-shaped assertion sites across
`test_runner.py` and `test_memory_store.py`; returning `RepairRunView` means
rewriting them all — test churn without a bug, exactly what the candidate-07
precedent rejects. What remains (typing only the Master's summary dict) is the
bare typing cleanup 07 already declined. **Dropped; recorded in the roadmap.**

## Behavior to preserve (guarded by tests)

- All daemon suites pass unchanged (`test_evolution_control/loop_cycle/
  throttle/recovery`, `test_authoring_loop`, `test_vault_watch`) — they
  construct the classes, call `_maybe_run_cycle` directly, import
  `_should_cycle`, and poke `_thread`; all of that survives.
- Boots-paused semantics, persisted statuses, budgets, crash recovery.
- New `tests/test_daemon.py`: the gate, sync + async lifecycle, cycle-error
  swallow-and-continue, disabled-never-spawns, and the new evolution switch.

## Done when

- One lifecycle implementation; the three daemons hold only cycle work;
  `pytest -q` green; smoke gate green; CONTEXT.md gains the **Daemon loop**
  entry; the C4 daemons prose names the seam; the roadmap carries 16's drop
  note.
