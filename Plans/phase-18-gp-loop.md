# Phase 18 — GP Loop (autonomous): Implementation Plan

Date: 2026-06-01
Branch: `phase-18-gp-loop` (off `main` at `e0eba6a`)
Spec source: [UBONGO_BUILD.md](../UBONGO_BUILD.md) §Phase 18 (lines 1224–1253).
Tier: 5 — Self-Improvement (third phase).

## Context

Phases 16 and 17 built the two halves of the GP loop as on-demand REPL tools:
`/optimize <target>` generates a population of prompt variants into
`evolution_lineage`; `/evaluate <target>` scores a generation against the
held-out set and writes a fitness leaderboard to `evolution_evaluations`. Phase
18 turns the crank automatically: a **background task** repeatedly picks a
target, generates a new generation seeded from the previous one's survivors,
evaluates it, and records progress — **throttled** by a real rolling-hour call
budget and **pausable** via REPL commands. Promotions (acting on the winners)
remain Phase 19; Phase 18 only produces and ranks generations continuously.

Already in place (reused, not rebuilt):

- `evolution/generator.py` (5 mutation strategies), `evolution/lineage.py`
  (`record_variants`, generation/parent resolution), `evolution/targets.py`
  (the persona-target registry), `evolution/fitness.py` (`rank_cohort`),
  `evolution/sandbox.py` (`evaluate_target`, `CallBudget`).
- `store.py` lineage + evaluation accessors; `events.py` already declares the
  `evolution_generation` event name (emitted here for the first time).
- The store's singleton connection is `check_same_thread=False` autocommit, so a
  background thread can call `store.*` safely (already true since Phase 12a's
  `asyncio.to_thread` workers). `store._now()` honors `UBONGO_FAKE_NOW` — used to
  make the rolling-window throttle deterministic in tests.

## Decisions locked with the user

- **Auto-start: paused.** When the REPL launches with `evolution.enabled=true`,
  the loop thread starts but idles in `paused`; the user runs `/evolution
  resume` to begin spending. No surprise background LLM cost on launch. Status is
  persisted, so a restart comes back paused. (`evolution.enabled=false` → the
  thread never starts.)
- **Pacing/cron: integer seconds.** `evolution.cron` is `null` (continuous,
  bounded only by the hourly throttle) or an integer = minimum seconds between
  cycle starts. No cron parser, no dependency, deterministic and testable.

## Goal

A background asyncio-driven task runs GP generations across the evolvable
targets — staleness-ordered, throttled to `max_calls_per_hour` over a real
rolling-hour window, paced by `evolution.cron`, and controllable via
`/evolution status | pause | resume | off`. Progress lives entirely in SQLite, so
killing the REPL mid-generation and restarting resumes from the last completed
generation.

## Architecture: pure cycle + thin scheduler + daemon thread

The hard part is running continuous background work alongside a **synchronous**
`input()` REPL. The design splits three concerns:

1. **`evolution/loop.py::run_one_cycle(*, budget) -> CycleResult`** — pure and
   synchronous, no sleeps. Picks the stalest target, generates one generation
   (seeded from survivors), evaluates it under `budget`, records an
   `evolution_runs` row, emits `evolution_generation`. This is what the tests
   drive directly. Returns calls spent + a summary.
2. **`evolution/loop.py::EvolutionLoop`** — the thin scheduler. A loop that, each
   tick: reads control status (`off`/`paused`/`running`); if running and the
   rolling-hour budget and `cron` interval allow, calls `run_one_cycle` (off the
   event-loop thread via `asyncio.to_thread`, mirroring the runner); then
   `await asyncio.sleep(tick)`. Sleep and clock are injectable so tests never
   wait real time. A `threading.Event` stop flag breaks the loop.
3. **Daemon thread** — `EvolutionLoop.start()` spins up a daemon thread running
   `asyncio.run(self._run())`. `repl.run()` starts it (when
   `evolution.enabled`) right after session setup and calls `stop()` (set the
   event, join with a short timeout) on every exit path. `daemon=True` so a
   mid-LLM-call cycle can never block process exit.

`__main__.py` is unchanged for `send` (one-shot never starts the loop); only the
REPL path runs it.

## Target + survivor selection (`evolution/selection.py`, 18a + 18c)

- **Target selection (staleness round-robin).** `next_target()` returns the
  evolvable target whose latest generation is oldest (or which has no generation
  yet), using `evolution_lineage.created_at` / the `evolution_runs` log. Equal
  staleness breaks by registry order. Over time this round-robins across
  `persona:architect | operator | casual` (scenario 4: round-robin visible in
  lineage timestamps).
- **Survivor selection (top-K).** After a generation is evaluated,
  `survivors(target, generation, k)` returns the top-K `lineage_id`s by fitness
  (via `store.evaluations_for_target`, already ranked fitness-desc /
  lineage-asc). `k = evolution.survivors` (new key, default 3).
- **Cross-generation lineage.** The next generation mutates **from a survivor's
  text**, not the base, with `parent_id` set to that survivor's `lineage_id`.
  Generation 1 (no prior survivors) still seeds from `targets.resolve_base`.

## Reusing generation + evaluation under one budget (18b)

A cycle shares a single `CallBudget` across both generation and evaluation so the
hourly cap is honored end-to-end:

- **Extend `generator.generate(target, n, *, budget=None, parent_text=None,
  parent_id=None)`** — when `budget` is given, check `budget.can_afford(1)` before
  each LLM strategy and stop when exhausted (`perturb_temperature` is free and
  always allowed). When `parent_text` is given, mutate from it instead of the
  base; the returned variants carry `parent_id` so `lineage.record_variants` can
  link them. (Phase 16 callers pass neither and are unaffected.)
- **Extend `lineage.record_variants(target, variants, *, parent_id=None)`** — when
  a per-variant `parent_id` is present, persist it (cross-generation lineage);
  otherwise keep Phase 16's `active_evolutions` resolution.
- Evaluation already takes a `CallBudget` (Phase 17). The cycle seeds the budget
  from the **rolling-hour remaining** (below), runs generation, then evaluation
  with whatever budget is left.

## Throttle — the real rolling-hour window (closes the Phase 17 deferral)

Phase 17 left a per-run cap; Phase 18 makes it a persistent rolling window:

- New **`evolution_runs`** table: one row per cycle — `id, target, generation,
  calls_spent, outcome ('completed'|'partial'|'aborted'), started_at, ended_at`.
- Before a cycle, `calls_in_last_hour()` sums `calls_spent` over rows with
  `ended_at` within the trailing hour (clock via `store._now()`); `remaining =
  max(0, max_calls_per_hour - that_sum)`. The cycle's `CallBudget(remaining)`
  bounds generation + evaluation together. If `remaining == 0`, the cycle is
  skipped and the scheduler sleeps.
- Scenario 3 (`max_calls_per_hour=5`): generation does ≤5 LLM calls then stops,
  evaluation gets 0 budget → 0 variants scored that cycle; total ≤5 in the
  window. Persisting `evolution_runs` means the window survives restart.

## Loop control state + crash recovery

- New **`evolution_state`** single-row table: `status ('running'|'paused'|'off'),
  updated_at`. Seeded `paused` on first launch. `/evolution resume|pause|off`
  update it; the scheduler reads it each tick. `evolution.enabled=false` short-
  circuits before the thread starts.
- **Crash recovery (scenario 5) is derive-from-DB.** A generation is "complete"
  when its variants have evaluations. On restart, `next_target()` /
  `survivors()` read the DB and the loop continues from the last completed
  generation. An interrupted cycle leaves an `evolution_runs` row stuck in
  `started`/`partial` and a generation with missing evaluations; the loop detects
  this and re-evaluates or advances rather than double-generating. No bespoke
  checkpoint file.

Both new tables go in `schema.sql` as `CREATE TABLE IF NOT EXISTS` — applied
automatically on the next `bootstrap()` for existing DBs, so **no ALTER
migration** is needed (the first Tier-5 schema addition, but a trivial one).

## REPL commands (`/evolution …`, 18d + 18e)

A subcommand dispatch mirroring the `/optimize` / `/evaluate` direct-tool pattern
(no `master.handle`):

- `/evolution status` — status (`running`/`paused`/`off`), per-target latest
  generation + best fitness, generations completed, calls in the last hour vs
  cap, last cycle time, next target. Reads the DB; works whether or not the
  thread is live.
- `/evolution resume` — set `running` (and, if `enabled=false`, tell the user to
  enable it in settings first).
- `/evolution pause` — set `paused`; in-flight cycle finishes, no new ones.
- `/evolution off` — set `off`; the loop idles until `resume`.

Helpers `_parse_evolution_command` + `_render_evolution_status` are unit-testable
without the REPL loop. Add `evolution` to `_HELP_COMMANDS`.

## Files touched

New:

- `src/ubongo/evolution/loop.py` — `run_one_cycle`, `EvolutionLoop` (scheduler +
  daemon thread), `CycleResult`, rolling-window throttle helper.
- `src/ubongo/evolution/selection.py` — `next_target`, `survivors`,
  staleness/top-K helpers.

Modified:

- `src/ubongo/evolution/generator.py` — optional `budget` / `parent_text` /
  `parent_id`.
- `src/ubongo/evolution/lineage.py` — optional explicit `parent_id`.
- `src/ubongo/memory/schema.sql` — `evolution_runs`, `evolution_state` tables.
- `src/ubongo/memory/store.py` — `append_evolution_run`, `calls_in_last_hour`,
  `get_evolution_status` / `set_evolution_status`, `evolution_runs_recent`.
- `src/ubongo/repl.py` — `/evolution` dispatch + parser + renderer; start/stop
  the loop thread in `run()`; help string.
- `config/settings.yaml` — document `cron` (int seconds | null) and add
  `survivors: 3`. (`enabled`, `max_calls_per_hour`, `generations_per_run`,
  `samples_per_eval` already exist.)

`__main__.py` is listed in the spec but needs no change beyond confirming the
loop is REPL-only (one-shot `send` must not start it).

## Tests

Unit (`tests/`), no real LLM, no real sleeps:

- `test_evolution_selection.py` — `next_target` picks the stalest / ungenerated
  target and round-robins; `survivors` returns top-K by fitness with the
  lineage-id tiebreak.
- `test_evolution_loop_cycle.py` — `run_one_cycle` with monkeypatched
  generate/evaluate: seeds gen 1 from base, gen 2 from survivors (parent_id set);
  writes an `evolution_runs` row; emits `evolution_generation`; respects the
  passed budget.
- `test_evolution_throttle.py` — `calls_in_last_hour` over `UBONGO_FAKE_NOW`
  timestamps; a cycle seeded from the remaining window does ≤cap calls (scenario
  3); `remaining==0` skips.
- `test_evolution_control.py` — `evolution_state` get/set; the scheduler skips
  cycles when `paused`/`off` and runs when `running` (injected sleep, bounded
  tick count, `enabled=false` never starts).
- `test_evolution_recovery.py` — a generation left un-evaluated is resumed (not
  double-generated) on the next `run_one_cycle` (scenario 5, derive-from-DB).
- `test_repl_evolution.py` — `/evolution status|pause|resume|off` parse + render;
  `evolution` in help.
- `test_evolution_generator.py` / `test_evolution_lineage.py` — extend for the
  new `budget` / `parent_text` / `parent_id` params (Phase 16 behavior
  unchanged when omitted).

Spec scenario coverage:

| # | Scenario | Covered by |
| --- | --- | --- |
| 1 | Loop runs → status shows 1+ generation | `test_evolution_loop_cycle` + `test_evolution_control` (bounded scheduler) |
| 2 | Pause → no new generations | `test_evolution_control` |
| 3 | Throttle ≤ N calls in window | `test_evolution_throttle` |
| 4 | Multi-target round-robin in timestamps | `test_evolution_selection` |
| 5 | Crash recovery resumes last generation | `test_evolution_recovery` |

## Smoke additions

Append a Phase 18 section to `tests/manual/smoke_test.md`: with
`evolution.enabled=true`, launch the REPL → `/evolution status` shows `paused`;
`/evolution resume` → after a short wait `/evolution status` shows ≥1 generation
completed and calls-in-last-hour climbing; `/evolution pause` halts new
generations; set `max_calls_per_hour: 5` and confirm a cycle stays within the
window; kill the REPL mid-cycle and restart → status resumes from the last
completed generation, no duplicate. (Live steps need `OPENROUTER_API_KEY`; the
bounded behavior is covered by the unit suite.)

## Out of scope (later phases)

- Acting on winners — promotions, `/improvements`, `pending_promotions` /
  `active_evolutions` writes, live-target swap, the audit log → Phase 19.
- Evolvable routing / tool-chain / retry-strategy targets (only persona targets
  evolve here) → Phase 19.

## Branch workflow

1. `git switch -c phase-18-gp-loop` off `main` at `e0eba6a`.
2. **First commit also flips STATUS.md** Phase 17 → Complete (merged via PR #9,
   `e0eba6a`) — the deferred doc fix from the handoff — alongside `Plan: Phase
   18 — GP Loop`. Push; open a **draft** PR titled `Phase 18 — GP Loop
   (autonomous)`, base `main`, linking this plan.
3. Implement in order: schema + store state/throttle → selection → generator /
   lineage parent extensions → `run_one_cycle` → `EvolutionLoop` + thread wiring
   → `/evolution` REPL → cron pacing, tests alongside each.
4. Full pytest (currently 584 green; Phase 18 adds ~30) + the Phase 18 smoke
   section; a bounded live run (resume, watch one generation, pause).
5. Mark ready; user reviews, user merges. Do not start Phase 19 until merged.

## Acceptance

- All five spec scenarios pass; smoke section passes; full pytest green.
- Background loop starts paused, runs on `resume`, pauses/offs on command;
  throttled to a real rolling-hour window; paced by `cron` seconds.
- Progress is DB-derived; restart resumes the last completed generation.
- `/evolution status|pause|resume|off` work; `evolution_generation` emitted.
- New tables via `CREATE TABLE IF NOT EXISTS` (no ALTER); Phase 16/17 behavior
  unchanged when the new generator/lineage params are omitted.
- LOC stays well under the 15k soft target.
