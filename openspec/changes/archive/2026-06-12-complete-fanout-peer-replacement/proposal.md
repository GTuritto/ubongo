## Why

Phase 13's plan (`Plans/phase-13-repair.md` §13c) specified peer-agent replacement on agent failure across all five fan-out execution modes — parallel, competitive, collaborative, debate, speculative. The shipped implementation wired the `_maybe_replace_failed` helper into only two of them (`_run_parallel`, `_run_collaborative`). Competitive, debate, and speculative have no recovery path: a failed producer silently degrades the turn via `any_failure = True`. This closes that gap so the Repair Agent's recovery contract holds uniformly across every execution mode, as the approved plan intended.

## What Changes

- Wire the existing `_maybe_replace_failed` helper into `_run_competitive`, `_run_debate`, and `_run_speculative` in `src/ubongo/runner.py`, matching the placement already used in `_run_parallel` and `_run_collaborative` (between the agent dispatch and the `any_failure = True` branch).
- Speculative mode: apply peer replacement to the cheap-or-strong leader slot, consistent with plan §13c (the non-leader side already serves as a natural fallback, so only the leader needs the helper).
- Competitive and debate: a failed producer's slot is filled by its configured peer before ranking / synthesis, so the failure does not shrink the candidate set or drop a debater.
- A recovered fan-out failure in these three modes writes the same audit trail as the existing two — one `repair_runs` row (`strategy='replace_with_peer'`) and one `agent_runs` row (`retried=True`) for the peer.
- No new configuration: peer mappings continue to come from `settings.yaml::agents.repair.peer_replacements`. No multi-strategy retry in fan-out modes — peer replacement only, unchanged from Phase 13's locked scope.
- New tests covering peer replacement (and the unrecoverable no-replace case) in competitive, debate, and speculative.

No breaking changes. This is additive: modes that previously degraded on failure now attempt one peer substitution first.

## Capabilities

### New Capabilities
- `fanout-peer-replacement`: Peer-agent substitution when an agent fails inside a fan-out execution mode (parallel, competitive, collaborative, debate, speculative). Defines when a peer is dispatched, which slot it fills, what audit rows are written, and the fall-through behavior when no peer is configured or the failure is unrecoverable.

### Modified Capabilities

(none — no existing spec files in `openspec/specs/`)

## Impact

- **Code**: `src/ubongo/runner.py` — `_run_competitive`, `_run_debate`, `_run_speculative` gain a `_maybe_replace_failed` call site. The helper itself is unchanged.
- **Tests**: `tests/test_runner.py` — new cases for peer replacement and unrecoverable-no-replace in the three modes.
- **Docs**: `Plans/phase-13-repair.md` and `STATUS.md` updated to reflect that all five fan-out modes now carry peer replacement.
- **No impact** on settings schema, the `repair_runs` table, the Repair Agent's `plan_recovery`, sequential-mode recovery, or the WriteBuffer. Scope is confined to three runner coroutines.
