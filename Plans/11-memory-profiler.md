# Phase 11 — Memory profiling (`/profile mem`)

Branch: `improve/11-memory-profiler`. Approved in-session 2026-06-11: complete
the local profiler's trio (performance / CPU / memory) with a tracemalloc
half, same design language as candidate 10.

## Problem

Candidate 10 covers performance (stats over the run tables) and CPU (opt-in
cProfile). Memory is uncovered, and it is the dimension that matters for
Ubongo's shape: a long-lived REPL plus three background daemons is where a
leak accumulates (an unbounded cache, growing conversation context, embedding
buffers). There is no way today to answer "what grew since I armed it".

## Solution

Extend `src/ubongo/profiling.py` with a tracemalloc baseline-and-diff half,
stdlib only, opt-in, zero overhead when off.

### 11a — profiling.py memory half

Module-level session state (single-user CLI; one armed session at a time):

- `mem_start()` — `tracemalloc.start()`, take and hold a baseline
  `Snapshot`. Idempotent: re-arming replaces the baseline.
- `mem_report(top_n=15) -> str | None` — None when not armed; otherwise
  snapshot now, `compare_to(baseline, "lineno")`, render the top-N growth
  sites (file:line, size delta, count delta), plus `tracemalloc.
  get_traced_memory()` (current, peak) and process RSS via
  `resource.getrusage` (ru_maxrss is bytes on macOS, KB on Linux — normalize).
- `mem_stop()` — `tracemalloc.stop()`, clear the baseline.
- `mem_active() -> bool` — for status.
- Filter the report's own frames and tracemalloc internals out of the diff
  (`Snapshot.filter_traces`), so the profiler does not report itself.
- Best-effort like the CPU half: report assembly failures degrade to a logged
  warning, never an exception into the REPL loop.

### 11b — `/profile mem` commands

Extend `_parse_profile_command` / `_cmd_profile` in `repl.py`:

- `/profile mem on` — arm; message warns that tracemalloc adds per-allocation
  overhead while armed (this is why it is opt-in, mirrors cpu wording).
- `/profile mem` — the growth report since baseline (the main command).
- `/profile mem off` — disarm and clear.
- `/profile mem status` — armed/off plus current traced/RSS when armed.
- No one-shot flag: leak hunting needs a session that stays alive; one-shot
  exits before a diff means anything. Noted here as a deliberate non-feature.

No new tables, no event handlers, no Memory Agent writes, no ReplState field
(the tracemalloc state is process-global by nature; the module owns it).

## Behavior to preserve

- `/profile` family from candidate 10 unchanged (parser stays backward
  compatible; `cpu` and `mem` are sibling subcommands).
- Zero overhead when off: tracemalloc is never started unless armed.
- A profiling failure never breaks a turn or a command.

## Testing

- `tests/test_profiling.py` additions: parser accepts `mem`/`mem on|off|status`
  and rejects garbage; `mem_report()` is None when unarmed; armed →
  allocate a large object → report names this test file as a growth site;
  `mem_stop()` clears (report None again, tracemalloc not tracing);
  re-arm replaces baseline; report-failure path swallowed (monkeypatch).
- Smoke playbook: new rows in the candidate-10 section (arm, turn, report,
  disarm).
- CONTEXT.md: extend the "Profiler" glossary entry to the trio.

## Done when

- `pytest -q` fully green (900 + new).
- Live REPL: `/profile mem on`, a turn, `/profile mem` shows growth sites,
  `/profile mem off` disarms.
- PR ready; user merges.

## Estimated size

~90 LOC in profiling.py + ~40 in repl.py, ~80 LOC tests.
