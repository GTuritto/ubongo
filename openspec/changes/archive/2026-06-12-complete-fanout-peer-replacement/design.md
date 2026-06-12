## Context

Phase 13 added the `_maybe_replace_failed` helper in `src/ubongo/runner.py` (lines 487–575). It asks the Repair Agent for the first recovery plan, and if that plan is `REPLACE_WITH_PEER`, dispatches the configured peer in the failed agent's slot, persisting one `repair_runs` row and one `agent_runs` row. The helper is fully implemented and already in use by `_run_parallel` (runner.py:711) and `_run_collaborative` (runner.py:949).

Phase 13's plan (`Plans/phase-13-repair.md` §13c task 3) specified the helper for all five fan-out modes. `_run_competitive`, `_run_debate`, and `_run_speculative` never received the call. Today a failed producer in those three modes falls straight through to `any_failure = True`: a competitive turn ranks over a shrunk candidate set, a debate turn loses a voice, a speculative turn loses its leader. The helper requires no change — only three new, near-identical call sites.

## Goals / Non-Goals

**Goals:**
- Invoke `_maybe_replace_failed` for failed producers in competitive, debate, and speculative modes, matching the placement and result-swap pattern already established in `_run_parallel`.
- Keep the audit trail identical across all five modes (one `repair_runs` row, one `agent_runs` row per recovery).
- Update the helper's docstring (which lists only four modes) and `Plans/phase-13-repair.md` / `STATUS.md` to state that all five fan-out modes carry peer replacement.

**Non-Goals:**
- Multi-strategy retry inside `asyncio.gather`. Fan-out modes stay single-hop peer replacement only; the sequential ladder is unchanged.
- New peer mappings or config keys. Mappings stay in `settings.yaml::agents.repair.peer_replacements`.
- Touching `_maybe_replace_failed`'s internal logic, the `repair_runs` schema, or `RepairAgent.plan_recovery`.

## Decisions

**Competitive — replace after `gather`, before ranking.** `_run_competitive` collects results at runner.py:815, then computes `ok_pairs`. Insert a replacement loop between those two steps, copied from the `_run_parallel` loop (runner.py:707–728): for each failed result, call `_maybe_replace_failed`; on a recovered `peer_result.ok`, swap `results[i]` and the corresponding name so the recovered candidate enters `ok_pairs` and the evaluator ranks over it. Alternative considered: replace before `gather` by pre-checking — rejected, failures aren't known until dispatch completes.

**Debate — replace a failed debater before synthesis.** `_run_debate` runs debaters over N rounds, then a synthesizer. The replacement applies to the debater dispatch results so a failed debater's slot is filled before its argument feeds the synthesizer. Decision: apply replacement to round-one debater failures (the debater set is fixed at round one); a debater that fails mid-rounds is replaced for the remainder of that turn. This keeps the synthesizer's input at full width.

**Speculative — replace the leader only.** `_run_speculative` runs cheap and strong concurrently and picks a leader. Per spec, peer replacement is scoped to the leader slot: if the side selected as leader failed, attempt replacement; if the leader succeeded, do nothing even when the other side failed (the successful leader already satisfies the turn). This avoids a wasted `repair_runs` row for a failure that did not affect the outcome — consistent with plan §13c's note that speculative "already has a natural fallback path."

**Reuse the helper verbatim.** All three call sites pass the same keyword arguments `_run_parallel` passes. No new parameters, no behavior branches inside the helper. This keeps the five modes provably consistent and the diff mechanical.

## Risks / Trade-offs

- **Debate round semantics** → The debater set and round structure must be read carefully so replacement targets the right dispatch results. Mitigation: a test that fails a debater and asserts the peer's contribution reaches synthesis; if the round loop makes single-point replacement awkward, scope replacement to round one only and document it.
- **Speculative leader identification** → "Leader" is computed after both sides return; the replacement must key off the chosen leader, not a fixed index. Mitigation: a test for both cases (leader fails → replaced; non-leader fails → not replaced).
- **Extra latency on recovery** → A peer dispatch adds one agent round-trip on the failure path only. Acceptable: it already holds for parallel and collaborative, and the alternative is a silently degraded turn.

## Migration Plan

Additive and behind the failure path — no migration. Modes that previously degraded on failure now attempt one peer substitution first. Rollback is reverting the three call sites; the helper and its callers in parallel/collaborative are untouched. No schema or config change.

## Open Questions

- Debate: replace only round-one debater failures, or also mid-round failures? Default in this design is round-one set membership with the peer carried for the rest of the turn; revisit if a test shows the round loop makes that awkward.
