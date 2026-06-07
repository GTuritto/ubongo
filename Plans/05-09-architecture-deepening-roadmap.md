# Plan — Architecture deepening roadmap (Candidates 05–09)

Source: the `/improve-codebase-architecture` report, 2026-06-07. Five deepening
candidates, ordered into five phases. Each phase is its own branch
(`improve/0N-<short-name>`) and its own draft PR, opened right after the first
commit (per CLAUDE.md branch workflow). Phases land one at a time; do not start
phase N+1 until phase N is merged.

This file is the roadmap. When a phase starts, lift its section into a standalone
`Plans/0N-<short-name>.md` (matching 01–04) so the PR can link it.

## Vocabulary

Architecture terms per `LANGUAGE.md` (module, interface, depth, shallow, seam,
leverage, locality). Domain terms per `CONTEXT.md` (Worker Agent, Model call,
Variant, Execution mode, Workflow). No drift into "service / component / boundary."

## Ordering and dependencies

```
05 envelope ──► 06 metadata        (06 reads the type 05's envelope wants; do as a pair)
07 variant-row    (independent — evolution tier only)
08 router-plan    (independent — orchestration core)
09 turn-body      (speculative; do LAST; easier once 05/06 land)
```

- **05 → 06** are the same `run()` seam and should ship back-to-back (06 may even
  precede 05 on one branch; see Phase 2 note). Highest leverage, lowest risk.
- **07** and **08** are independent of the agent seam and of each other; either can
  follow 06 in any order.
- **09** is `Speculative` in the report. Attempt only after 05/06 prove the pattern.

---

# Phase 05 — Collapse the worker-agent model-call envelope

Branch `improve/05-agent-llm-envelope`. Strength: **Strong**.

## Problem

Every LLM Worker Agent re-implements the same envelope around one
`llm.complete()` call: monotonic timer, `override_model` / `max_tokens_override`
resolution, `repair_prompt_hint` append, `try/except LLMError →
AgentResult(ok=False, error="<name>_llm_error")`, the `logger.info("<name>_run", …)`
line, and the success `AgentResult` assembly. Confirmed identical in
`agents/coding.py:53-109` and `agents/critic.py:67-138`, repeated in
`research.py`, `personas.py`, and `evaluator.py` (plus `evaluator.rank` and
`evaluator.agree`). ~8 copies. Each `run()` is **shallow**: the envelope is most
of it; the only real difference is prompt assembly.

## Solution

One deep seam — `agents/llm_run.py: run_agent_llm(*, agent_name, build_prompt,
messages, input, default_model, default_max_tokens) -> AgentResult` — owns the
envelope. The shared `llm.complete()` seam (single retry, token/latency
accounting, `before_llm`/`after_llm` events) is unchanged. Each agent's `run()`
shrinks to a `build_prompt` callback plus the call. **Prompt assembly, the
repair-hint append, and result interpretation stay in `run()`** (this is the line
that keeps the change consistent with CONTEXT.md "Model call").

### Sub-phases

- **05.1** Add `agents/llm_run.py` with `run_agent_llm`. Pure unit tests against a
  fake `complete`: success path, `LLMError` path (asserts `ok=False`,
  `error="<name>_llm_error"`, latency recorded), model/max-tokens resolution.
- **05.2** Migrate the composers first — `coding.py`, `personas.py` — since their
  text becomes the response; verify composer behavior unchanged.
- **05.3** Migrate the helpers/validators — `research.py`, `critic.py`.
- **05.4** Migrate `evaluator.py`: `run`, then the two extra envelopes in `rank`
  and `agree`. Confirm the evaluator-confidence signal is untouched.
- **05.5** Leave `execution.py` out (no LLM call; `default_model = ""`). Note it
  explicitly in the PR so the asymmetry is intentional, not missed.

## Behavior to preserve (guarded by tests)

- Error string contract `"<name>_llm_error"` per agent (governance and repair key
  off agent failure; do not rename).
- The `logger.*` event names (`coding_run`, `critic_run`, …) — STATUS smoke and
  any log assertions depend on them. Keep them parameterized by `agent_name`.
- `composer` attribute semantics and last-composer-wins.

## Risks / ADR / CONTEXT check

- **CONTEXT.md "Model call"** states there is "no separate invocation /
  `call_model` abstraction layer." This relocates only the mechanical envelope,
  not prompt assembly, so it completes the seam's stated job rather than adding a
  layer. **Action:** during this phase, update the "Model call" entry to describe
  the envelope seam (same discipline the skill applies). Get explicit sign-off on
  the wording before merging.
- No ADR governs agent internals; no ADR conflict.

## Done when

All LLM agents call `run_agent_llm`; the envelope exists once; full pytest green;
Phases 0–21 cumulative smoke passes; CONTEXT.md "Model call" updated.

---

# Phase 06 — Type the agent metadata seam

Branch `improve/06-agent-directives`. Strength: **Strong**. Depends on / pairs
with Phase 05.

## Problem

`AgentInput.metadata` is an untyped `dict` (`agents/base.py:25`). The real
interface is ~7 string keys written by the runner — `"override_model"`
(`runner.py:113`), `"repair_prompt_hint"` (377), `"max_tokens_override"` (379),
`"debate_role"` (901, 947), `"persona"`, `"skill"` — and read by `.get(str)` in
six agents (`coding`, `critic`, `research`, `personas`, `evaluator`, `execution`
via `"exec_command"`). ~22 sites, no declared type. A typo returns `None` and the
behaviour silently does not happen; no test fails.

## Solution

A frozen dataclass `AgentDirectives` carried on `AgentInput` (replacing or
wrapping `metadata`). The runner constructs it; agents read attributes. What the
orchestrator may tell an agent becomes the type definition. Pairs with 05: the
`run_agent_llm` envelope reads `input.directives.override_model /
.max_tokens_override / .repair_prompt_hint` instead of dict `.get()`.

### Sub-phases

- **06.1** Define `AgentDirectives(override_model, max_tokens_override,
  repair_prompt_hint, debate_role, persona, skill, exec_command)` in `base.py`,
  frozen, all defaulting to `None`. Decide: replace `metadata` outright vs. keep a
  thin `metadata` for forward-compat. (Recommend replace — the report's whole
  point is removing the untyped seam.)
- **06.2** Update the runner's three construction sites (`_dispatch_agent_async`
  base build, the repair `extra_metadata` path, the debate role injections) to
  build `AgentDirectives`.
- **06.3** Update the six agents' reads to attribute access. With 05 done, most of
  this is inside `run_agent_llm`; agent `run()` bodies only touch agent-specific
  fields (`debate_role`, `skill`, `exec_command`).
- **06.4** Update tests that construct `AgentInput(metadata={...})` to the typed
  form; add a test asserting an unknown field is a construction error, not a
  silent drop.

## Behavior to preserve

- Every current key keeps its meaning and effect (model override, token cap,
  repair hint, debate role, skill, exec command).
- Repair's same-model retry path (`repair_prompt_hint` + `max_tokens_override`).

## Risks / ADR check

- No ADR conflict. Note in the PR: this is the typed restatement of the
  Phase-13b repair-hint convention, not a behavior change.

## Done when

`AgentInput` carries `AgentDirectives`; no agent reads a string key; pytest green;
smoke passes.

> **Sequencing note:** 06 may be done *before* 05 if preferred — then 05's envelope
> is written against the type from the start. Either way ship them as a pair.

---

# Phase 07 — Give the Variant a row type  — DROPPED (premise disproven 2026-06-07)

Branch `improve/07-variant-row`. Strength: **Worth exploring** → **not pursued**.

> **Correction (2026-06-07).** The 07.1 investigation disproved this candidate's
> only concrete justification. The claimed `"mutation"` vs `"strategy"` key
> mismatch is **not a bug**: `lineage.py:43` persists every variant's metadata as
> `{"strategy": variant.strategy, **variant.metadata}`, so a top-level
> `"strategy"` key is always present and `manual.py`'s `.get("strategy")` reads it
> correctly. The `"mutation"` key on config variants is *additional* provenance
> (the full strategy name), not a substitute. The original report over-claimed
> this. What remains is a mild, no-bug typing cleanup (variant rows are untyped
> `list[dict]` with the shape in a docstring), which did not justify a phase next
> to the higher-value Phase 08. Not pursued.

## Problem (original, retained for the record)

GP Variants flow through the evolution tier as raw `list[dict]`. The shape is
documented only in `selection.survivors`'s docstring ("Each dict carries
`lineage_id`, `variant_text`, `fitness`, `strategy`"); `sandbox.evaluate_target`
takes `variant_rows: list[dict]` and reaches in with `row["variant_text"]`;
`targets`, `generator`, `loop`, `manual` all assume the shape. **Leaky seam.**
~~Concrete symptom: the generator writes config provenance under `"mutation"`
and manual.py reads `"strategy"`.~~ — DISPROVEN, see correction above.

## Solution

A `VariantRow` dataclass with a `from_row(dict)` parsed once at the store read
boundary. Downstream reads `.variant_text`, `.fitness`, `.strategy` as attributes.
The metadata-blob keys (`base_source`, `kind`, `mutation`/`strategy`,
`temperature_delta`) get named in one place, forcing the mutation/strategy split
to be reconciled.

### Sub-phases

- **07.1** First, confirm whether the `mutation` vs `strategy` mismatch is a live
  bug (does `manual.leaderboard` show config-variant strategies today?). Capture
  the finding; it justifies the phase or narrows it.
- **07.2** Define `VariantRow` + `VariantRow.from_row` in the evolution package
  (e.g. `evolution/variant.py`). Single source for column + blob-key names.
- **07.3** Convert the store read boundary so rows become `VariantRow` once
  (`selection.survivors`, the `evaluations_for_target` reads `sandbox`/`loop` use).
- **07.4** Migrate consumers (`sandbox.evaluate_target`, `loop`, `manual`,
  `generator` seeding) to attribute access; reconcile the strategy key.
- **07.5** Replace the selection docstring shape contract with the type.

## Behavior to preserve (per ADR-0002)

- `store.py` stays the **only raw-SQL writer**. `VariantRow` is built *from* store
  rows at the read boundary; no SQL moves out of the single-writer module.
- Fitness ranking, survivor selection, lineage `parent_id` seeding all unchanged.

## Risks / ADR check

- ADR-0002 respected (above). ADR-0006/0007 untouched (fitness model, target
  kinds unchanged). If 07.1 shows the mismatch is a real bug, fixing it is part of
  this phase, not a separate one.

## Done when

Variant rows are `VariantRow` from the read boundary inward; the strategy key is
single-sourced; pytest green (incl. evolution suites); smoke passes.

---

# Phase 08 — Plan the Workflow inside the router

Branch `improve/08-router-plan-workflow`. Strength: **Worth exploring**. Independent.

## Problem

`master.plan` (`master.py:194-253`) builds one `Workflow` by calling the router
seven times (`route_workflow`, `workflow_persona`, `workflow_agents`,
`workflow_mode`, `workflow_evaluate` + conditional evaluator append,
`workflow_rounds`, `workflow_timeout_s`) and owns the combination logic. The
runner then **re-validates** mode invariants at execute time (e.g. competitive
must end in `evaluator`, else `ValueError`). The router is shallow; the mode/agents
invariant is split across master and runner.

## Solution

One deep `router.plan_workflow(classification, *, persona, auto_mode,
pending_workflow) -> Workflow` that owns the seven reads and enforces mode/agents
invariants at plan time. The per-fact reads become its implementation.

### Sub-phases

- **08.1** Add `router.plan_workflow` wrapping the existing seven reads; return a
  validated `Workflow`. Keep the seven functions for now (internal callers).
- **08.2** Move the mode/agents invariant checks from the runner's strategy
  coroutines to plan time (validate once when the Workflow is built). Leave a
  thin assertion in the runner as a backstop if cheap.
- **08.3** `master.plan` delegates to `router.plan_workflow`. **Hysteresis and the
  `/mode`/`pending_workflow` override stay in master** (they read turn state, not
  config) — pass `persona`/`pending_workflow` in; the router does config only.
- **08.4** Tests: a bad mode/agents combo now raises at plan time; the
  effective-config precedence (eval-override > promotion > file) still resolves
  correctly through the new entry point.

## Behavior to preserve (per ADR-0003 / ADR-0008)

- classify → plan → execute pipeline shape unchanged; no bypass paths.
- Effective-config precedence intact (eval-override > promotion > file); live-swap
  reads still consulted inside the router.
- `/mode` override and persona hysteresis behavior identical.

## Risks / ADR check

- ADR-0003 and ADR-0008 explicitly preserved (above). This deepens `plan` only.
  No conflict; flag in PR that the pipeline is untouched.

## Done when

`master.plan` is one `router.plan_workflow` call; invariants validated at plan
time; pytest green; smoke passes (esp. `/mode` and competitive routing).

---

# Phase 09 — Decompose the turn body into testable steps  — NOT PURSUED

Branch `improve/09-turn-steps`. Strength: **Speculative** → **not pursued**.

> **Decision (2026-06-07).** Roadmap closed after Phase 08. The two Strong
> candidates (05, 06) shipped, plus the higher-value Phase 08 (router
> consolidation). 07 was dropped (premise disproven) and 09 is Speculative with
> seam-design risk; not worth it next to the value already captured. Retained
> below as a record of the idea, not a committed plan.

## Problem

`master.handle` / `_handle_with_buffer` (~`master.py:314-643`) runs the whole turn
in one body — classify, plan, execute, critic-retry + repair, govern + interactive
gate, compose, staged buffer commit — interleaved with store/events/queue side
effects. The hard behaviour lives in control flow (critic retry reuses the
`workflow_run_id`; a gated decision replaces `result.text`; the assistant message
is staged then committed only on success). Tests reach leaf functions, not the
orchestration where the bugs live.

## Solution

Extract each phase as a function that takes data and returns data (`repair_outcome`,
`decision`, `composed`); the body sequences them; side effects move to the edges.
The order becomes testable.

### Sub-phases

- **09.1** Characterization tests FIRST — pin current end-to-end behavior
  (commit-on-success, gated-text replacement, repair run-id reuse) before moving
  anything. This phase is risky; the safety net comes first.
- **09.2** Extract `decide`/govern assembly into a pure step returning a
  `Decision` + the (possibly gated) text, no I/O.
- **09.3** Extract the repair/critic-retry lifecycle into a step returning a
  `repair_outcome`, preserving the shared `workflow_run_id`.
- **09.4** Extract compose + the staged-commit boundary; keep the
  `workflow_buffer` commit-on-success semantics (ADR-0002).
- **09.5** Reduce `_handle_with_buffer` to sequencing + edge I/O; add a test
  asserting step order.

## Behavior to preserve (per ADR-0002 / ADR-0003)

- Single write path; commit-on-success buffer; no partial rows on failure.
- No bypass paths; every run/decision still persisted.
- Interactive approval/repair prompts stay synchronous `input()`/`print()`
  (ADR-0002 carve-out), not routed through the queue.

## Risks / ADR check

- Highest-risk phase; mitigated by 09.1 characterization tests. Defer until 05/06
  are merged and the team is comfortable with the new agent seam.
- No ADR conflict (internal shape only).

## Done when

The turn body is a sequence of data-returning steps; step order is asserted by a
test; full pytest green; full Phases 0–21 smoke passes.

---

## Cross-cutting checklist (every phase)

- Branch `improve/0N-<name>` off latest `main`; draft PR opened after the first
  commit (the `Plan: …` commit), base `main`, body links the lifted
  `Plans/0N-<name>.md`.
- Per-phase: full `pytest` green + Phases 0–21 cumulative smoke (`tests/manual/
  smoke_test.md`) before marking the PR ready.
- LOC budget: all five are net-neutral-to-negative (deepening removes
  duplication); stay well under the ~15k soft cap.
- User reviews and merges; do not self-merge; do not start N+1 until N merges.

## Out of scope

- Any v0.2 Telegram work (additive, separate).
- The governance scorers (`risk`/`confidence`/`reversibility`) — assessed deep
  enough; ADR-0004 deliberately chose the code-based matrix. Not re-litigated.
- New execution modes, new agents, new evolvable targets.
