# Phase 10 — Local profiler (`/profile`)

Branch: `improve/10-local-profiler`. Approved in-session 2026-06-11 (brainstormed
design: on-demand SQL aggregation + opt-in cProfile; "Both" profiler types,
opt-in CPU trigger, stdlib only).

## Problem

Ubongo records latency and token data on every turn — `agent_runs.latency_ms`,
`tokens_in/out`, `workflow_runs.started_at/ended_at` — but nothing aggregates
it. `/trace` shows individual runs; there is no view of where time and tokens go
across runs (per agent, per model, per execution mode), and no way to find CPU
hot spots in the process itself.

## Solution

One new module, `src/ubongo/profiling.py`, two halves, no new tables, no event
handlers, no writes to durable memory, stdlib only.

### 10a — Stats aggregation (read-only over existing tables)

Pure functions in `profiling.py` that run read-only SELECTs via
`store.connection()` (single-writer rule intact: reads only) and return small
dataclasses; rendering helpers in the same module mirror `_render_trace` style.

- `summary(last_n=None)` — turn count, avg + p95 workflow latency (computed
  from `started_at`/`ended_at`; `workflow_runs` has no latency column), total
  tokens in/out, slowest agent by total latency.
- `by_agent(last_n=None)` — per `agent_runs.agent`: runs, avg/p95 `latency_ms`,
  tokens out, failure rate (`outcome != 'success'`), retried count.
- `by_model(last_n=None)` — same grouped by `agent_runs.model`.
- `by_mode(last_n=None)` — per `workflow_runs.execution_mode`: runs, avg/p95
  workflow latency, outcome split.
- `last_n` filters to the most recent N `workflow_runs` (by id); default all.
- p95 computed in Python from the fetched latency list (single-user scale).
- Empty DB renders "No runs recorded yet." — never errors.

### 10b — CPU profiling (opt-in cProfile)

- `ReplState` (commands.py) gains `cpu_profile: bool = False` (defaulted, so
  existing constructions and tests stay valid).
- `profiling.profile_call(fn, *args, **kwargs)` wraps a call in
  `cProfile.Profile()` inside try/finally — a profiling failure can never break
  the turn. Writes `data/profiles/turn-<YYYYmmdd-HHMMSS>.prof` (dir derived
  from `store.get_db_path().parent`, honoring test `set_db_path`) and returns
  `(result, report_text)` where report is a top-25 cumulative `pstats` summary.
- REPL: when `state.cpu_profile`, the turn's `master.handle` call routes
  through `profile_call`; the report prints after the response. `master.handle`
  is resolved at call time so test patch targets are preserved.
- One-shot: `ubongo send --profile "msg"` (flag on the `send` subparser in
  `__main__.py`; `oneshot.run(message, persona, profile=False)` keyword with a
  default so existing callers/tests are untouched).
- Known limitation (accepted): cProfile sees the whole process including
  event-loop idle time; fine for a single-user CLI, avoids a py-spy dependency.

### 10c — `/profile` command

Registry entry in `repl.py` `COMMANDS` following the `Handler` contract:

- `/profile [N]` — summary (optionally over last N workflow runs)
- `/profile agents|models|modes [N]` — breakdown tables
- `/profile cpu on|off|status` — arm/disarm/report the cProfile toggle
- Bad args -> usage string with `_HELP_COMMANDS`, matching siblings.

## Behavior to preserve (guarded by tests)

- Existing `ReplState(...)` constructions compile unchanged (new field defaulted).
- `oneshot.run(message, persona)` positional contract unchanged.
- Tests that patch `master.handle` keep working (no import-time binding).
- No writes outside `data/profiles/`; SQLite opened for reads only.

## Testing

- `tests/test_profiling.py`: aggregation against a seeded temp DB
  (`store.set_db_path`) — multi-agent/multi-model/multi-mode fixtures, p95
  correctness, `last_n` filtering, empty-DB message, failure-rate math.
- Command parsing/rendering: `/profile`, subcommands, bad input -> usage.
- CPU: `profile_call` returns the wrapped result, writes a loadable `.prof`
  (pstats can read it), and swallows profiler-side errors.
- New section in `tests/manual/smoke_test.md`: `/profile`, `/profile agents`,
  one `/profile cpu on` turn, `ubongo send --profile`.

## Risks / ADR / CONTEXT check

- Single-writer rule: read-only access; profiles directory is not the vault.
- New module + command -> CONTEXT.md glossary entry for "profiler".
- No ADR needed: no contract supersedes; this is an additive diagnostic
  surface, same family as `/trace`.

## Done when

- `pytest -q` fully green (723 + new).
- `/profile` family works in a live REPL against the real `data/ubongo.db`.
- `/profile cpu on` turn writes a `.prof` and prints a top-25 summary.
- `ubongo send --profile "hello"` does the same in one-shot.
- PR ready for review; user merges.

## Estimated size

~350 LOC (`profiling.py` + wiring) + ~150 LOC tests.
