# Plan — Architecture deepening roadmap, second pass (Candidates 14–19)

Source: the `/improve-codebase-architecture` evening report, 2026-06-11 (run at
v0.1.4, after the smoke gate, the CI/CD pipeline, and the MCP server channel
merged). Six candidates ordered into six phases. Each phase is its own branch
(`improve/1N-<short-name>`) with a draft PR opened right after the first commit
(per CLAUDE.md branch workflow); phases land one at a time — do not start phase
N+1 until phase N is merged.

This file is the roadmap. When a phase starts, lift its section into a
standalone `Plans/1N-<short-name>.md` so the PR can link it. The first roadmap
(05–09) is the precedent, including its honest endings: 07 was dropped when its
premise was disproven, 09 closed as speculative — record the same way here if a
candidate dies.

## Vocabulary

Architecture terms per the review skill's LANGUAGE.md (module, interface,
depth, shallow, seam, leverage, locality, deletion test). Domain terms per
`CONTEXT.md` (channel, Worker Agent, Model call, Execution mode, Profiler, MCP
channel). No drift into "service / component / boundary."

## Versioning rule

These are behavior-neutral deepenings: **no VERSION bump per phase** (so the
release pipeline stays quiet), matching how the 05–09 refactors folded into the
v0.1.0 changelog entry. They ship to users inside the next feature release's
bundle (v0.1.5 MCP client, or v0.2 Telegram). The CHANGELOG gets a line under
the next feature version's entry, not its own.

## Ordering and dependencies

```
14 channel-core ──► (v0.2 Telegram builds ON this seam)
15 daemon-loop      (independent; same "extract the proven envelope" move)
16 repair-view      (small; ride along with 14 or 15, or its own micro-phase)
17 runner-fanout    (independent; cosmetic-leaning — do when touching runner anyway)
18 command-packs    (independent; best AFTER 14 so repl.py sheds turn-envelope code first)
19 store-package    (conditional; only if v0.2 work grows store.py — else drop)
```

- **14 first.** It is the only candidate on the v0.2 critical path: Telegram is
  "a new transport, additive on the existing seams," and 14 *builds* that seam.
  Do it before any v0.1.5/v0.2 channel work so the new transport starts as a
  thin adapter instead of a fourth envelope copy.
- **15** is independent and can interleave anywhere; it also pre-builds the
  seam a future Telegram poller daemon would want.
- **16** is deliberately small. Default: fold into 14 or 15 as a sub-phase
  rather than spending a whole phase (the 07 precedent says bare typing
  cleanups do not earn phases; this one rides, not stands).
- **17/18** are quality-of-reading improvements; schedule opportunistically.
- **19** has an explicit trigger condition, not a slot. No trigger → drop and
  record.

Interleaving with feature work: v0.1.5 (MCP client) and v0.2 (Telegram) take
precedence when you want them — but land 14 before starting either transport.

---

# Phase 14 — One channel core behind the four fronts

Branch `improve/14-channel-core`. Strength: **Strong** (the report's top
recommendation; new friction created 2026-06-11 by the MCP layer itself).

## Problem

Every channel re-implements the same turn envelope by hand. `web/turn.py:22-42`
and `mcp/service.py:52-71` are name-substituted twins: the same `_bootstrapped`
flag, the same `resolve_startup_profile(None, os.environ.get("UBONGO_PROFILE"))`,
the same `<channel>_cpu_profiling_on` log. The profiled-turn block
(`profiling.profile_call(master.handle, …)` + report log + `queue.flush_delivered`)
repeats at `oneshot.py:32`, `web/turn.py:58`, `mcp/service.py:86`, with a fourth
variant in the REPL loop at `repl.py:1435`. The no-bypass contract
(ADR-0002/0003: every channel through `master.handle`, queue flushed) lives only
as a convention each new channel re-honors. Tonight's live-caught worker-thread
bug was an envelope bug; its regression test protects only the MCP copy.

## Solution

A `src/ubongo/channel.py` module with two functions:

- `bootstrap()` — config + logging once + `UBONGO_PROFILE` knob resolution
  (idempotent; starts no daemons).
- `run_turn(message, persona, auto, *, approved=False) -> Response` — the
  profile-wrap (report first-line logged), `master.handle` resolved at call
  time (test patches survive), `queue.flush_delivered`.

Channels keep only their presentation: one-shot's printing/exit codes and its
mem-report flow, web's Streamlit rendering and Approve/Deny, MCP's TypedDict
shaping and `anyio.to_thread` hop, the REPL's interactive prompts and per-state
toggles (the REPL's cpu toggle is per-session state, so it passes its own
profile decision in — design the parameter, don't special-case).

### Sub-phases

- **14.1** Characterize: pin current behavior of all three channel modules with
  the existing suites; note every patch target tests use today.
- **14.2** Extract `channel.bootstrap` + `channel.run_turn`; port one-shot.
- **14.3** Port web/turn and mcp/service (their modules shrink to presentation
  + the worker-thread hop); move the worker-thread regression test to the seam.
- **14.4** REPL: route the loop's profiled-turn branch through the same core.
- **14.5** Docs: CONTEXT.md "channel" glossary entry sharpened; C4 channels
  note updated; ADR only if the seam's shape contradicts ADR-0015 wording (not
  expected).

## Behavior to preserve (guarded by tests)

- Every existing channel test passes unchanged (patch targets preserved via
  call-time resolution and re-exports where needed).
- Gated turns: identical text + `gated` semantics per channel.
- `UBONGO_PROFILE` behaves identically on all channels by construction.
- The MCP worker-thread hop stays (the core is sync; MCP awaits it in a thread).

## Done when

- The envelope exists once; the three channel modules contain no bootstrap or
  flush logic; `pytest -q` green; smoke gate green (it exercises every channel).
- Estimated size: ~80 LOC new module, net negative overall.

---

# Phase 15 — One daemon lifecycle module behind the three loops

Branch `improve/15-daemon-loop`. Strength: **Strong** (carried from the morning
report; citations re-verified at v0.1.4).

## Problem

Three background daemons re-implement the same lifecycle. `_should_cycle` is
byte-identical in `evolution/loop.py:170-188` and `authoring/loop.py:101-114`;
the lifecycle classes (`__init__/start/stop/_thread_main/_run/_maybe_run_cycle`)
differ only by name substitution; `VaultWatcher` (`vault_watch.py:75-110`) is
the same shape minus the gate. Live drift symptom: authoring and the watcher
have env off-switches; evolution has none — the suite cannot silence all three
the same way.

## Solution

A `DaemonLoop` module: `DaemonLoop(name, cycle, gate_inputs, *, sleep, tick)`
with `start() -> bool` / `stop(timeout)`, owning the thread, the stop event,
the per-cycle exception swallow, and the shared budget/status/cron gate (one
`_should_cycle`). Each daemon supplies its `run_one_cycle` and gate inputs;
status tables, config loading, and the watcher's config-driven interval stay
put. Add the missing `UBONGO_DISABLE_EVOLUTION` switch as part of the
unification. REPL start/stop call sites untouched.

### Sub-phases

- **15.1** Extract `DaemonLoop` + the shared gate; port EvolutionLoop.
- **15.2** Port AuthoringLoop and VaultWatcher (watcher passes no gate).
- **15.3** Off-switch parity (`UBONGO_DISABLE_EVOLUTION`); conftest note.
- **15.4** CONTEXT.md glossary ("Daemon loop"), C4 daemons prose touch.

## Behavior to preserve

- Boots-paused semantics, persisted statuses, rolling-hour budgets, crash
  recovery (all pytest-gated today — those suites must pass unchanged).
- Injectable sleep/tick test seams keep working (now proven once at the seam).

## Done when

- The three daemons hold only their cycle work; one lifecycle implementation;
  the duplicated ~90 lines are gone; `pytest -q` green; `/evolution` +
  `/authoring` smoke rows unchanged.

---

# Phase 16 — Type the repair read seam (ride-along)

Default: a sub-phase of 14 or 15, NOT its own branch. Strength: **Worth
exploring**, with the candidate-07 precedent standing against it as a
standalone phase.

## Problem

`store.repair_runs_for_workflow()` (`store.py:789`) returns `list[dict]`; the
Master folds the last row into a second untyped dict (`master.py:160-166`) and
reads it back by string key (`master.py:395-399`) — while the same table
already has a typed view, `RepairRunView` (`memory/trace.py`), used by the
trace path. Two consumers, one typed: the seam exists and is bypassed.

## Solution

`repair_runs_for_workflow()` returns `list[RepairRunView]`; the Master's
summary becomes a frozen `RepairSummary` carried on `Response`. The repair
ladder and the apology template are untouched.

## Done when

- One row contract; a renamed column fails at construction, not as an em-dash
  in the apology. If neither 14 nor 15 wants the rider, record a drop note
  here rather than forcing a phase.

---

# Phase 17 — Shrink the dispatch block in the runner's fan-out modes

Branch `improve/17-runner-fanout` (or fold into any phase already touching the
runner). Strength: **Worth exploring**.

## Problem

Nine near-verbatim 14-line argument blocks invoke `_dispatch_agent_async`
across the six modes (`runner.py:547-562` and `625-640` are verbatim twins;
also `751`, `811`, `885`, `931`, `1005`, `1012`). Only 2-3 arguments ever vary
in fan-out (`prior_findings=[]`, `override_model=None`, `retried=False` never
do). The logic is already deep (invoke.py, the envelope, `_dispatch_agent_async`
itself); this is call-site plumbing.

## Solution

Default the invariant parameters on `_dispatch_agent_async`, or add a
`_fanout(resolved, *, message, history, …)` helper returning the task list.
The repair-recovery call sites (`362`, `375`, `479`) stay explicit — there the
"invariants" are exactly what varies. Mode semantics untouched.

## Done when

- Mode bodies read as strategy, not plumbing; ~70 lines gone; zero behavior
  change (the mode suites pass unchanged). Honest note: the deletion test here
  is mild — no logic reappears — so this phase is cheap or skipped, never
  expensive.

---

# Phase 18 — Let each subsystem own its command pack

Branch `improve/18-command-packs`. Strength: **Worth exploring**. Do AFTER 14
(repl.py first sheds the turn-envelope code 14 extracts).

## Problem

`repl.py` is 1,490 lines (1,440 at the morning report — it absorbs every
feature's commands) holding 66 `_parse_*`/`_render_*`/`_cmd_*` functions for
five subsystems: evolution (~170 lines), authoring (~130), governance/trace
(~150), memory (~100), profiler (~110). Every subsystem change edits the file
that owns the turn loop. The registry seam (`commands.py`, candidate 04)
already makes commands data.

## Solution

Per-subsystem command modules exporting registry fragments
(`evolution/commands.py`, `authoring/commands.py`, `memory/commands.py`, a
core/governance pack); `repl.py` merges fragments into `COMMANDS` and keeps
the loop, interactive prompts, `emit`, and the persona/exit fallback. The
`Handler` contract and help-banner derivation are untouched. Re-exports keep
existing test patch targets (`from ubongo.repl import _render_x`) valid.

## Done when

- repl.py under ~600 lines and shrinking toward its actual job; an authoring
  command change touches `authoring/`; `pytest -q` green with zero test-site
  rewrites; help banner byte-identical.

---

# Phase 19 — Store package split (CONDITIONAL — has a trigger, not a slot)

Branch `improve/19-store-package` only if triggered. Strength: **Speculative**.

## Trigger

v0.2 (Telegram) or v0.1.5 (MCP client) adds tables/accessors that push
`memory/store.py` (1,990 lines, 83 public functions, 14 domains) meaningfully
past 2,000 lines. If the file stops growing, **drop this phase and record the
drop here** (the 09 precedent).

## Problem / Solution (when triggered)

Width, not shallowness: one module is the read path of nearly every change.
Split into `memory/store/` (`_connection.py` + per-domain modules) with
`__init__.py` re-exporting all existing names — every call site and
`patch("…store.X")` target survives verbatim. The deletion test on the split
itself is neutral; the payoff is locality and AI-navigability only.

## Done when

- Same 83-name interface, internal seams per domain, suite green untouched.

---

## Non-goals (checked in the report; do not re-litigate)

- The MCP package's service/server split, `ubongo-ctl.sh`'s `[web|mcp]`
  generalization, smoke.sh and the workflows: already deep or out of scope.
- The repair ladder seam, result typing, master's pass-throughs, the
  evolution/authoring symmetry, the three deliberate raw-SQL sites: verified
  deep in both passes.
- Turn-body decomposition (old candidate 09): stays closed.
