# Plan — Candidate 01: Let Repair own the recovery ladder

Architecture-review deepening candidate **01** (Strong, top recommendation) from
[`docs/architecture-review-2026-06-05.md`](../docs/architecture-review-2026-06-05.md).
Branch: `improve/01-repair-recovery-ladder` (off `main`).

## Problem

The recovery ladder — Repair's logic — lives in the runner. Two runner methods import
Repair's private taxonomy (`Strategy`, `_classify_failure`) and branch on the enum:

- `runner._recover_or_give_up` (`runner.py:308–494`) — sequential mode walks the **full
  ladder**: classify once, loop `plan_recovery()` → branch on `ABORT` /
  `REPLACE_WITH_PEER` / retry, dispatch, persist a `repair_runs` row per attempt. ~150 lines.
- `runner._maybe_replace_failed` (`runner.py:498–587`) — the five fan-out modes take a
  **single peer hop**: first plan only, act if `REPLACE_WITH_PEER`. Called at 5 sites
  (723, 834, 980, 1122, 1257).

So the runner knows the `Strategy` enum, the abort/peer/retry branching, attempt indexing,
and the per-attempt audit shape. That is Repair's decision logic, located in the runner.

## Solution

Repair exposes **one** recovery interface and drives the ladder internally. The runner
supplies two thin callbacks (how to *run* an attempt, how to *persist* an attempt) and
says which strategies are permitted via an `allow` scope. The per-mode asymmetry that
ADR-0003 settles is preserved by the scope argument, not by two methods.

### New surface in `agents/repair.py`

```python
class RecoveryScope(Enum):
    LADDER = "ladder"        # sequential: walk the full per-kind ladder
    PEER_ONLY = "peer_only"  # fan-out: single peer hop, no retry loop

@dataclass(frozen=True)
class RepairAttempt:           # one audit record handed back to the runner to persist
    failure_kind: str
    original_error: str | None
    strategy_attempted: str
    peer_agent: str | None
    override_model: str | None
    attempt_index: int
    outcome: str               # "recovered" | "failed" | "aborted"
    started_at: str
    ended_at: str | None

@dataclass(frozen=True)
class RecoveryOutcome:
    result: AgentResult        # final result (still ok=False on exhaustion)
    peer_agent: str | None     # set when a peer replaced the slot (fan-out relabel)

async def recover(
    self,
    *,
    agent_name: str,
    original: AgentResult,
    allow: RecoveryScope,
    dispatch: Callable[[RecoveryPlan], Awaitable[AgentResult | None]],
    persist: Callable[[RepairAttempt], None],
    clock: Callable[[], str],
) -> RecoveryOutcome: ...
```

`recover()` owns what the runner used to: classify once, walk `plan_recovery()`, branch on
the `Strategy` enum, index attempts, emit the abort/peer-not-registered audit rows, decide
when to stop. It calls `dispatch(plan)` to run each attempt and `persist(attempt)` to record
it. It imports nothing from the runner and nothing new (it already owns `Strategy`,
`_classify_failure`, the ladders).

**`dispatch` contract:** runner-supplied closure that turns a `RecoveryPlan` into an
`AgentResult`. Returns `None` to mean "this plan could not be executed" (peer named but not
registered) — `recover()` then persists a `failed` row and stops, exactly as today.

**`persist` contract:** runner-supplied closure wrapping `_persist_repair_run`, so the
**actual store write still originates in the runner** (tracing stays runner-owned; ADR-0002
single-writer untouched — see Risks). `recover()` never imports `store`; timestamps come
via the injected `clock` (`store.now_iso`).

### Runner changes

- Delete `_recover_or_give_up` and `_maybe_replace_failed`. Drop the two
  `from ubongo.agents.repair import Strategy, _classify_failure` imports.
- Add one mechanical helper `_run_recovery(*, agent_name, original_result, scope, message,
  history, summary_text, prior_findings, workflow, context, workflow_run_id) ->
  RecoveryOutcome` that:
  - builds the `dispatch` closure: if `plan.peer_agent` set → look up the peer in
    `self.registry` (return `None` if missing — the peer-not-registered signal), dispatch it
    with `retried=True`; else re-dispatch the original `agent` with `override_model` +
    `prompt_hint`→`extra_metadata` + `max_tokens_cap`. This is the only place that still
    knows `_dispatch_agent_async`'s many params.
  - builds the `persist` closure: `RepairAttempt` → `_persist_repair_run(workflow_run_id=…,
    agent_name=…, **fields)`.
  - calls `await repair.recover(...)` and returns the `RecoveryOutcome`.
  This helper carries **no** taxonomy knowledge — no `Strategy`, no `_classify_failure`.
- Sequential call site (636): `result = (await self._run_recovery(..., scope=LADDER)).result`.
- Five fan-out call sites: `outcome = await self._run_recovery(..., scope=PEER_ONLY)`; replace
  the old `if replaced is not None: peer_result, peer_name = replaced` with
  `if outcome.peer_agent and outcome.result.ok: results[i] = outcome.result; ...`.

### Behavior to preserve exactly (guarded by existing 58 runner + 34 repair tests)

- Sequential walks the full per-kind ladder; classification re-runs against the **original**
  error after a peer attempt, against the **latest retry** result after a same-agent retry
  (mirrors current lines 443–446 / 465).
- `attempt_index`, `outcome` strings (`recovered`/`failed`/`aborted`), the single ABORT row
  on `max_attempts`/`ladder_exhausted`, and the peer-not-registered `failed` row are emitted
  identically.
- Fan-out does a single peer hop only; no retry loop inside `asyncio.gather`.
- The `repair is None or not hasattr(repair, "plan_recovery")` guard (no-Repair path)
  returns the original result with no recovery, as today.

## Tests

- Existing `tests/test_runner.py` (58) and `tests/test_agents_repair.py` (34) must stay green
  unchanged — they exercise recovery through the mode methods / full workflows, so they
  validate behavior preservation.
- Add direct unit tests for `recover()` in `tests/test_agents_repair.py` using fake
  `dispatch`/`persist`/`clock` (no runner fixture needed — this is the stated testability
  win): full-ladder walk to recovery, ladder exhaustion → ABORT row, single peer hop
  (PEER_ONLY), peer-named-but-not-registered → `failed` row + stop, `max_attempts` cap,
  no-op when first plan isn't `REPLACE_WITH_PEER` under PEER_ONLY.
- Full suite green (`pytest`), then the cumulative smoke test (`tests/manual/smoke_test.md`).

## Risks / ADR check

- **ADR-0003 (per-mode asymmetry).** Preserved: `RecoveryScope.LADDER` vs `PEER_ONLY` is the
  asymmetry, now expressed once behind the seam instead of as two methods. Decision stands;
  only the *location* of the loop moves.
- **ADR-0002 (single-writer / single-connection).** Unchanged. The `persist` callback runs in
  the runner and calls the existing `_persist_repair_run` → `store.append_repair_run`; Repair
  gains no store dependency and writes nothing itself.
- **Blast radius.** Contained to `runner.py` + `repair.py`. No store schema, no config, no
  other module. This is the lowest-risk-highest-value candidate and de-risks Candidate 02.

## Out of scope

Candidates 02–06. The shared `invoke_agents()` core (02) is the natural next branch and is
unblocked by this one, but is not part of it.

## Done when

- `recover()` is the single recovery interface; runner imports no Repair taxonomy.
- 58 + 34 existing tests green, new `recover()` unit tests added and green, full suite green,
  smoke test passes.
- Draft PR opened against `main`, marked ready once the above hold.
