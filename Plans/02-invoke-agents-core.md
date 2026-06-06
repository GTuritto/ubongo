# Plan — Candidate 02: One agent-invocation core under the modes and the eval sandbox

Architecture-review deepening candidate **02** (Worth exploring; the review's "biggest
payoff, biggest blast radius — do it after Candidate 01") from
[`docs/architecture-review-2026-06-05.md`](../docs/architecture-review-2026-06-05.md).
Branch: `improve/02-invoke-agents-core` (off `main`, which now has candidates 01 + 03).

## Problem

The "run a list of agents" logic is re-implemented seven times:

- Six runner mode methods (`runner.py:421-1148`): each repeats **agent resolution** (iterate
  `workflow.agents`, skip `"repair"`, registry lookup, warn+skip on missing), **dispatch**
  (the same ~12-kwarg `_dispatch_agent_async` call), **fan-out peer recovery** (the
  per-failed-result `_run_recovery(scope="peer_only")` loop — verbatim in parallel,
  competitive, collaborative; inline in debate, speculative), and **harvest** (last_ok /
  last_composer-by-`composer` / evaluator_confidence / any_failure).
- A **seventh copy** in `evolution/sandbox.py::_run_workflow_isolated` (`:342-373`): the same
  sequential prior-findings-threading + composer-pick loop, bare (no events / agent_runs /
  recovery), used by the config evaluator. It silently drifts from `runner._run_sequential`.

## Constraint that shapes the design

`sandbox.evaluate_target` runs **synchronously on the GP loop's event-loop thread**
(`evolution/loop.py:109`, no `await`), while the runner is async. A single async
`invoke_agents` is therefore unsafe: a nested `asyncio.run` inside the sandbox would raise
"cannot be called from a running event loop". So the shared core is **sync and
dispatch-agnostic**. What duplicates and drifts is the loop's *decision logic* (resolve,
prior-threading, composer/confidence/tokens/any_failure). *Dispatch* legitimately differs —
async + side-effectful (runner) vs sync + bare (sandbox) — and stays the per-caller seam.

## Solution

A new side-effect-free module **`src/ubongo/invoke.py`** that owns the shared decision
logic; modes and the sandbox keep only their distinct dispatch + orchestration.

```python
# invoke.py — no store, no events, no registry-mutation; pure decision logic.

def resolve_agents(registry, names) -> list[tuple[str, Agent]]:
    """Skip 'repair', look up each name, warn + drop missing. The resolution
    loop all six modes + the sandbox repeat."""

@dataclass
class InvokeOutcome:
    results: list[tuple[str, Agent, AgentResult]]   # run order
    last_ok: AgentResult | None
    last_composer: AgentResult | None               # last result whose agent.composer
    evaluator_confidence: float | None              # last result.confidence seen
    any_failure: bool
    total_tokens: int
    prior_findings: list[str]                        # threaded ok-texts
    @property
    def composer_text(self) -> str: ...              # last_composer or last prior (sandbox rule)

class SequentialHarvest:
    """Stateful sync harvester: the prior-threading + composer/confidence/tokens
    /any_failure semantics that runner._run_sequential and the sandbox share.
    Caller drives the loop and dispatches (async or sync); after each dispatch it
    calls observe(); at the end it reads outcome()."""
    def __init__(self, *, thread_prior: bool = True): ...
    @property
    def prior(self) -> tuple[str, ...]: ...          # feed into the NEXT dispatch
    def observe(self, agent: Agent, result: AgentResult) -> None: ...
    def outcome(self) -> InvokeOutcome: ...
```

The loop shape both callers run becomes:

```python
h = SequentialHarvest()
for name, agent in resolve_agents(registry, names):
    result = <dispatch>(name, agent, h.prior)   # await _dispatch_agent_async (+recovery)  |  agent.run bare
    h.observe(agent, result)
out = h.outcome()
```

### Runner changes

- `_run_sequential` → resolve via `invoke.resolve_agents`, drive a `SequentialHarvest`; its
  dispatch closure runs `_dispatch_agent_async` and, on failure, the `_run_recovery(scope=
  "ladder")` step (recovery folded into dispatch, so the loop body is the shared shape).
  `_build_workflow_result` is fed from `outcome`.
- Add two runner helpers to absorb the fan-out duplication (all async, same context):
  - `_dispatch_one(name, agent, *, prior_findings, extra_metadata=None)` — binds the common
    `_dispatch_agent_async` kwargs (message/history/summary/workflow/context/workflow_run_id/
    override_model=None/retried=False) captured per call. Cuts each ~12-line call to one.
  - `_recover_fanout_failures(names, agents, results)` — the per-failed-result
    `_run_recovery(scope="peer_only")` loop shared verbatim by parallel/competitive/
    collaborative (returns updated results + swapped agents). debate/speculative keep their
    inline single-hop (different control flow) but call `_dispatch_one`.
- `_run_parallel` harvest uses `invoke` (resolve + an `InvokeOutcome`-style harvest over the
  gathered results). competitive/collaborative/debate/speculative keep their mode-specific
  orchestration (rank / merge / rounds / race) but stop repeating resolve + dispatch kwargs +
  the fan-out recovery loop.

### Sandbox change (the anti-drift win)

`_run_workflow_isolated` → `resolve_agents` + a `SequentialHarvest`; its dispatch is the bare
`agent.run` in a try/except (a crash skips that agent — preserved by not calling `observe`
for the crash, or observing a synthetic non-ok result with 0 tokens). Returns
`outcome.composer_text, outcome.total_tokens`. It now provably shares the runner's
prior-threading + composer-pick semantics — it cannot drift.

## Behavior to preserve exactly (guarded by tests, all via public entry points)

- Sequential: prior-findings threading order, composer = last `composer=True` agent, evaluator
  confidence = last `result.confidence`, `any_failure` on any non-ok or missing agent.
- Fan-out modes: identical agent_runs, peer-replacement, ranking/merge/transcript/race outputs.
- Sandbox `_run_workflow_isolated`: same `(composer_text or last prior, total_tokens)`; crashes
  still skip the agent; tokens still summed for returned (even non-ok) results, not for crashes.
- `_dispatch_agent_async` (events + agent_runs persistence) is unchanged and stays the runner's
  dispatch; `invoke.py` never imports store/events (side-effect-free, per ADR-0007).

## Tests

- Existing suite green unchanged — modes are tested via `execute()` / full workflows
  (`test_runner.py`), the sandbox via `test_evolution_config_eval.py`. These are the
  behavior-preservation gate.
- New `tests/test_invoke.py`: unit-test `resolve_agents` (skip repair, drop missing) and
  `SequentialHarvest` (prior threading, composer pick across multiple composers, confidence
  carry, any_failure, tokens, `composer_text` fallback) with fake agents — no runner/sandbox
  fixture needed (the same testability win candidates 01/03 gave).
- Full suite green, then the full live smoke (all six modes + a GP/config-eval path).

## Risks / ADR check

- **ADR-0003 (six modes) & ADR-0007 (side-effect-free config eval).** Both reinforced: all six
  modes stay; the sandbox's isolated executor becomes a *shared* side-effect-free core instead
  of a parallel copy, which is what ADR-0007 already intends. `invoke.py` has zero side effects.
- **Biggest blast radius of the three candidates.** Mitigated by: (1) behavior-preservation
  held by the existing mode/sandbox tests + full live smoke; (2) sync core avoids the
  async-in-event-loop hazard; (3) dispatch stays per-caller, so side-effect wiring is untouched.
- Sequencing: done after 01 (recovery already behind `recover()`/`_run_recovery`, so the
  fan-out recovery helper wraps a clean seam rather than the old taxonomy-leaking loop).

## Out of scope

Candidates 04, 05, 06. Merging the mode-specific orchestration (rank/merge/rounds/race) into
the core — those are genuinely distinct collaboration shapes and stay as thin adapters.

## Done when

- `invoke.py` is the single home for resolve + sequential harvest; `_run_sequential` and
  `sandbox._run_workflow_isolated` both drive it; fan-out modes share `_dispatch_one` +
  `_recover_fanout_failures`; no mode repeats the resolve/dispatch/harvest boilerplate.
- Existing suite green unchanged, new `test_invoke.py` green, full suite green, full live
  smoke (six modes + config-eval) passes.
- Draft PR opened against `main`, marked ready once the above hold.
