## 1. Competitive mode

- [x] 1.1 In `_run_competitive` (`src/ubongo/runner.py`), insert a peer-replacement loop between `results = await asyncio.gather(*tasks)` and the `ok_pairs` computation, mirroring the `_run_parallel` loop (runner.py:707–728): for each failed result call `_maybe_replace_failed`; on a recovered `peer_result.ok`, swap `results[i]` and the corresponding entry in `competitor_names`/`competitor_agents`.
- [x] 1.2 Verify the recovered candidate enters `ok_pairs` and is passed to `evaluator.rank`.

## 2. Debate mode

- [x] 2.1 In `_run_debate` (`src/ubongo/runner.py`), add a `_maybe_replace_failed` call for failed debater dispatch results so a failed debater's slot is filled before synthesis; scope replacement to the round-one debater set per design.md, carrying the peer for the rest of the turn.
- [x] 2.2 Verify the peer's contribution reaches the synthesizer input at full debater width.

## 3. Speculative mode

- [x] 3.1 In `_run_speculative` (`src/ubongo/runner.py`), attempt `_maybe_replace_failed` only for the leader slot: if the side chosen as leader failed, attempt replacement; if the leader succeeded, attempt nothing even when the other side failed.
- [x] 3.2 Verify a failed leader is replaced and a failed non-leader (with a succeeding leader) triggers no replacement and no `repair_runs` row.

## 4. Docs

- [x] 4.1 Update the `_maybe_replace_failed` docstring (runner.py:504–507) to list all five fan-out modes including speculative.
- [x] 4.2 Update `Plans/phase-13-repair.md` (§13c and non-goals) and `STATUS.md` to state that peer replacement covers all five fan-out modes.

## 5. Tests

- [x] 5.1 `tests/test_runner.py`: competitive — failed candidate is replaced by its peer and enters the ranking set; `repair_runs` row has `strategy_attempted='replace_with_peer'`, `outcome='recovered'`; peer `agent_runs` row has `retried=True`.
- [x] 5.2 `tests/test_runner.py`: competitive — failed candidate with no configured peer is not replaced; existing `any_failure` degradation preserved.
- [x] 5.3 `tests/test_runner.py`: debate — failed debater is replaced and the peer's contribution reaches synthesis.
- [x] 5.4 `tests/test_runner.py`: speculative — failed leader is replaced; result text comes from the peer.
- [x] 5.5 `tests/test_runner.py`: speculative — non-leader fails while leader succeeds; no peer dispatched, no `repair_runs` row.
- [x] 5.6 `tests/test_runner.py`: unrecoverable failure (e.g. `execution_refused`) in competitive/debate/speculative is not replaced.
- [x] 5.7 Run `uv run pytest` — full suite green, including the new cases.

## 6. Smoke playbook

- [x] 6.1 Append competitive/debate/speculative peer-replacement rows to the Phase 13 section of `tests/manual/smoke_test.md`, referencing the new test names, and bump the expected pytest count in scenario 13.14.
