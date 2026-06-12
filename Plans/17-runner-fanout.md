# Phase 17 — Shrink the dispatch block in the runner's fan-out modes

Branch: `improve/17-runner-fanout`. Lifted from
[Plans/14-19-architecture-deepening-roadmap.md](14-19-architecture-deepening-roadmap.md).
Strength: **Worth exploring** — the roadmap's own framing: "cheap or skipped,
never expensive." Behavior-neutral: **no VERSION bump**.

## Problem

Nine near-verbatim argument blocks invoke `_dispatch_agent_async` across the
six modes (`runner.py:547-562` parallel and `625-640` competitive are verbatim
twins; also `751`, `811`, `885`, `931`, `1005`, `1012`). In fan-out,
`prior_findings=[]`, `override_model=None`, and `retried=False` never vary —
pure plumbing repeated nine times. The logic is already deep
(`_dispatch_agent_async` itself, `invoke.py`, the envelope); this is call-site
noise that hides each mode's actual strategy.

## Solution

Two small moves, no semantics changes:

1. **Defaults on the wrapper**: `prior_findings=()`, `override_model=None`,
   `retried=False` become defaulted keyword parameters of
   `_dispatch_agent_async` (it already tuples `prior_findings` internally, so
   an immutable default is safe). Call sites name only what differs.
2. **One fan-out helper**: `_fanout_tasks(resolved, *, message, history,
   summary_text, workflow, context, workflow_run_id)` returns the list of
   dispatch coroutines — the comprehension that parallel, competitive, and the
   collaborative producers repeat verbatim.

The repair-recovery call sites (`runner.py:362`, `375`, `479`) stay fully
explicit: there the "invariants" (override_model, retried, prior_findings)
are exactly what varies.

## Behavior to preserve (guarded by tests)

- All runner/mode/repair suites pass unchanged — the wrapper's behavior and
  the dispatched arguments are identical, only spelling at call sites changes.
- No new seam: `_fanout_tasks` is a private helper, not an interface.

## Done when

- The three verbatim comprehensions are one helper call each; the remaining
  direct sites name only their varying arguments; ~60-70 lines gone;
  `pytest -q` green; smoke gate green.
