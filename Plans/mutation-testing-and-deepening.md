# Plan — Mutation testing as a safety net, then deepen Strong candidates 01 & 03

Status: approved 2026-06-05. Branch `improve/mutation-testing-and-deepening` off `main`.
Source: [docs/architecture-review-2026-06-05.md](../docs/architecture-review-2026-06-05.md).

## Intent

Introduce mutation testing (mutmut 3.x) to this codebase, scoped first to exactly the
modules the two **Strong** architecture-review candidates touch. Surviving mutants
expose test-suite blind spots *before* any production code moves. Only once the net is
proven do the two refactors land, each re-checked against the same mutation gate.

One coherent branch. Mutation testing first; refactors second.

## Baseline facts

- 723 tests, ~37s full suite, Python 3.13, pytest-only (async via `asyncio.run`, no async plugin).
- Candidate 01 (Repair owns the recovery ladder): two files —
  [runner.py](../src/ubongo/runner.py) (`_recover_or_give_up` ~331-495, `_maybe_replace_failed` ~498-587),
  [repair.py](../src/ubongo/agents/repair.py).
- Candidate 03 (deepen trace read): one production caller
  ([repl.py:683](../src/ubongo/repl.py#L683) `_render_trace`, ~90 lines of dict
  navigation 682-771) + `store.last_n_workflow_runs` (store.py:871-1006) + 3 test sites
  (test_repl_trace.py, test_memory_store.py).

## Decisions

- Tool: **mutmut** (>=3, coverage-guided). Scope first pass to the refactor targets.
- Sequencing: mutation testing as safety net, then refactor 01 + 03. One track.
- Mutation bar: **100% minus documented equivalents** — kill every survivor that
  represents a real test gap; document any genuine equivalent (unkillable) mutants.

## Steps

1. **Wire mutmut (tooling only).** Add `mutmut>=3` to the `dev` group in
   [pyproject.toml](../pyproject.toml). Add `[tool.mutmut]` scoped to
   `repair.py` + `runner.py`. Add `scripts/mutation.sh` wrapper and
   `docs/mutation-testing.md`. First commit -> push -> open draft PR (do not merge).
2. **Baseline run + triage** on `repair.py` (pure deterministic logic — ideal first
   target). `mutmut run` then `mutmut results`; catalog survivors + `no_tests`.
3. **Kill survivors with TEST changes only** (zero production edits). Strengthen
   `tests/test_agents_repair.py` + runner recovery tests to the agreed bar. Commit
   before touching production code.
4. **Refactor Candidate 01.** Add `RepairAgent.recover(*, agent_name, original,
   dispatch, allow, ...)` owning the ladder (`Strategy`/`FailureKind`/attempts).
   - `dispatch`: async callback the runner supplies (closes over agent/message/history).
   - `allow`: `"full"` (sequential walks whole ladder) vs `"peer_only"` (fan-out single
     peer hop) — preserves ADR-0003 per-mode asymmetry, parameterized.
   - returns `(final_result, replaced_by, attempt_records)`; the **runner still persists**
     `repair_runs` rows (single-writer discipline). Only ladder logic moves.
   - Collapse runner `_recover_or_give_up` + `_maybe_replace_failed` into thin callers;
     runner stops importing `Strategy` / `_classify_failure`. Full suite + re-run mutmut.
5. **Refactor Candidate 03.** Add `store.py`/`last_n_workflow_runs` to mutmut scope; kill
   survivors. Introduce typed `WorkflowTrace` / `AgentRunView` / `GovernanceView` /
   `RepairRunView` (small `memory/trace.py` view). `last_n_workflow_runs` returns
   `list[WorkflowTrace]`; queries + grouping stay in the store. Rewrite `_render_trace`
   to read typed fields. Update 3 test sites. Field names mirror today's dict keys.
   Full suite + mutmut re-check.
6. **CI + docs.** Scoped nightly/manual mutation invocation (`mutmut run` +
   `mutmut export_cicd_stats`), documented (not a per-PR blocking gate — too slow).
   Update STATUS.md, smoke_test.md, and mark 01 & 03 addressed in the arch-review doc.

## Out of scope (this branch)

Candidates 02, 04, 05, 06. Candidate 05 touches ADR-0002 and wants its own decision.
No v0.2 product features.

## Risks

- mutmut runs in a copied `mutants/` dir — verify the `src/`-layout editable install
  resolves there on the first run before relying on it.
- Equivalent mutants documented, not chased.
