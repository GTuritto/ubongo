# Phase 17 — Sandboxed Evaluation + Fitness: Implementation Plan

Date: 2026-05-31
Branch: `phase-17-evaluation` (off `main` at `175c9ff`)
Spec source: [UBONGO_BUILD.md](../UBONGO_BUILD.md) §Phase 17 (lines 1194–1222).
Tier: 5 — Self-Improvement (second phase).

## Context

Phase 16 gave the GP layer a way to *generate* variants: `/optimize <target>`
mutates a persona prompt into 8 strategy-diverse alternates and parks each in
`evolution_lineage`. Phase 17 gives it a way to *judge* them: run each variant
against a held-out conversation set, measure five quality/cost signals, fold
them into a single fitness number, and rank. This closes the optimize → evaluate
half of the loop; Phase 18 automates the cycle and Phase 19 promotes winners.

Already in place (do not rebuild):

- **Schema.** [schema.sql](../src/ubongo/memory/schema.sql#L108) already defines
  `evolution_evaluations` (`lineage_id, sample_set, success_rate, cost,
  latency_ms, hallucination_rate, user_correction_rate, fitness, evaluated_at`).
  **No migration.**
- **Config.** [settings.yaml](../config/settings.yaml) ships
  `evolution.fitness_weights` (success_rate 0.40, cost_inverse 0.15,
  latency_inverse 0.10, hallucination_inverse 0.20, user_correction_inverse
  0.15 — sums to 1.0) and `evolution.max_calls_per_hour: 30` (deferred in
  Phase 16; wired here).
- **Lineage readers.** `store.lineage_for_target(target, generation)` and
  `store.max_lineage_generation(target)` from Phase 16 give us the variants to
  score.

Missing and created here: the fixture directory
`tests/manual/fixtures/sample_conversations.json` (referenced by CLAUDE.md but
not yet present).

## Goal

`/evaluate <target>` scores the variants of a target's latest generation against
the held-out sample set, writes one `evolution_evaluations` row per variant, and
prints a fitness-ranked leaderboard. A per-run LLM-call budget (seeded from
`max_calls_per_hour`) throttles cost and returns partial results when hit.
`/evaluate` with no argument lists evaluable targets.

## How a variant is evaluated

A variant is a persona prompt (`variant_text`). For each sample conversation:

1. **Generate.** Assemble the system prompt as `UBONGO.md` (global identity) +
   `variant_text` — the same top two layers
   [`context.build_system_prompt`](../src/ubongo/context.py#L31) uses, but with
   the variant body substituted for the persona file and **no skill / memory /
   agent-role layers**. That isolates the variant's effect. Call `llm.complete`
   with the sample's prior turns as `messages` and the persona's model.
2. **Judge.** One evaluation-judge LLM call scores the generated response on a
   rubric that returns all three subjective signals at once:
   `{"quality": 0.0..1.0, "hallucination": 0.0..1.0, "would_user_correct":
   true|false}`. One call per sample (not three) keeps cost down.
3. **Measure.** Record the generation call's `tokens_in + tokens_out` (cost
   proxy) and `latency_ms`.

Per variant, aggregate across the N samples evaluated:

| Metric | From | Direction |
| --- | --- | --- |
| `success_rate` | mean(quality) | higher better |
| `hallucination_rate` | mean(hallucination) | lower better |
| `user_correction_rate` | fraction with would_user_correct=true | lower better |
| `cost` | mean(total tokens) per sample | lower better |
| `latency_ms` | mean(latency) | lower better |

**Why a dedicated 3-signal judge rather than reusing `EvaluatorAgent`.** The
governance `EvaluatorAgent` returns only `{confidence, issues}`. Fitness needs
three separable subjective signals (quality, hallucination, correction); a
single judge call returning all three is both more faithful to the fitness
vector and cheaper than two or three `EvaluatorAgent`-style calls. The new judge
lives in `evolution/sandbox.py` and reuses the existing code-fence-tolerant JSON
parsing pattern; `EvaluatorAgent` is left untouched (it is on the governance hot
path and must not change behavior).

## "Sandboxed" = no side effects

`evolution/sandbox.py` is an **offline harness**, not the shell sandbox
(`src/ubongo/sandbox.py` is unrelated). Evaluation runs with **no** real side
effects: no `workflow_runs`, no `agent_runs`, no governance, no vault, no queue,
no durable-memory writes. It is pure functions + LLM calls. The only thing it
persists is the `evolution_evaluations` rows — the evaluation results
themselves. This isolation is what lets the GP loop (Phase 18) run continuously
in the background without polluting conversation state.

## Fitness (`evolution/fitness.py`)

`success_rate`, `hallucination_rate`, and `user_correction_rate` are already in
`[0, 1]`. `cost` and `latency_ms` are unbounded, so they are **min-max
normalized across the cohort** (the generation's variants evaluated in this run)
to `[0, 1]` before weighting — a variant is cheap/fast *relative to its
siblings*. The weighted sum, using the inverse weights for the
lower-is-better components:

```text
fitness = w_success            * success_rate
        + w_cost_inverse       * (1 - norm_cost)
        + w_latency_inverse    * (1 - norm_latency)
        + w_halluc_inverse     * (1 - hallucination_rate)
        + w_correction_inverse * (1 - user_correction_rate)
```

Weights load from `evolution.fitness_weights`. A single-variant cohort (no
spread to normalize) treats `norm_cost = norm_latency = 0` (best, since there is
nothing to compare against) — documented so the degenerate case is intentional,
not a divide-by-zero. **Tiebreak (scenario 4):** the leaderboard sorts by
fitness descending, then by `lineage_id` ascending — fully deterministic on
ties.

## Anti-cost safeguards (`17f`)

A `CallBudget` (in `evolution/sandbox.py`) caps the number of LLM calls in one
`/evaluate` run at `evolution.max_calls_per_hour`. Evaluation proceeds
variant-by-variant, sample-by-sample; before each `complete` call it checks the
budget. When the budget is exhausted it stops and returns **partial results** —
the variants fully evaluated so far get rows and appear on the leaderboard; the
rest are reported as skipped (`log`-style line in the render). Scenario 2
(`max_calls_per_hour=10` → throttle, partial results) is satisfied directly.

Note on scope: a true rolling-hour window across separate process runs is a
Phase 18 (autonomous loop) concern, where rate-over-time actually matters. Phase
17 implements a per-run call cap with the same config key; the plan flags this
explicitly so the limitation is not silent.

Cost is primarily bounded by a new **`evolution.samples_per_eval` (default 5)**
config key added to `settings.yaml`. With ~2 LLM calls per (variant, sample), an
8-variant generation at 5 samples is ~80 calls — so under the default
`max_calls_per_hour: 30` a single `/evaluate` fully scores ~3 variants and
reports the rest skipped, keeping a quick pass meaningful and cheap. Raising
`max_calls_per_hour` (or `samples_per_eval`) trades cost for a fuller run. The
per-target sample selection (affinity + general) is then truncated to
`samples_per_eval`, deterministically (stable fixture order, no random
sampling — randomness is neither reproducible nor available in this codebase).

## Persistence (`17d`, store accessors)

- **`store.append_evaluation(lineage_id, sample_set, success_rate, cost,
  latency_ms, hallucination_rate, user_correction_rate, fitness) -> int`** —
  one INSERT into `evolution_evaluations`, mirrors `append_lineage_variant`.
- **`store.evaluations_for_target(target, generation=None) -> list[dict]`** —
  joins `evolution_evaluations` to `evolution_lineage` on `lineage_id`, filters
  by target (and optionally generation), returns rows with the variant's
  strategy/metadata for the leaderboard. Ordered by fitness desc, lineage_id asc.
- **`store.latest_evaluation_for_lineage(lineage_id) -> dict | None`** — used to
  skip re-evaluating a variant already scored in this run.

## Held-out sample set (`17a`)

`tests/manual/fixtures/sample_conversations.json` — a JSON object:

```json
{
  "version": "default-v1",
  "conversations": [
    {
      "id": "arch-001",
      "persona_affinity": "architect",
      "turns": [
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."},
        {"role": "user", "content": "..."}
      ],
      "note": "what this probes"
    }
  ]
}
```

- 30+ short, anonymized conversations (no real names, emails, secrets). Curated
  to exercise the three personas; `persona_affinity` (`architect` / `operator` /
  `casual` / `null` for general) drives sample selection per target.
- The variant generates a response to the **last user turn**, with prior turns
  as context.
- A handful are **hallucination traps** — a user turn with a false premise or a
  request for a fact that cannot be known — so a degraded variant's
  `hallucination_rate` actually moves (scenario 3).
- `version` is written into the `sample_set` column so an evaluation is always
  traceable to the exact fixture it ran against.

Sample selection for a target: samples whose `persona_affinity` matches the
target persona plus the `null` (general) ones; if that yields nothing, fall back
to the full set. Bounded by `samples_per_eval`.

## REPL command (`/evaluate`, `17e`)

Mirrors Phase 16's `/optimize` dispatch in [repl.py](../src/ubongo/repl.py),
a direct tool (no `master.handle`):

- `/evaluate` (no arg) → list evaluable targets (those with at least one lineage
  row), via `_render_evaluate_targets`.
- `/evaluate persona:architect` → load the latest generation's variants, run the
  sandbox harness under the call budget, write `evolution_evaluations` rows,
  print a fitness leaderboard: rank, `#lineage_id`, strategy, fitness, and the
  component breakdown (success / halluc / corr / cost / latency). A trailing
  line notes any variants skipped by the budget.
- `/evaluate <unknown>` → `Unknown target` + the target list.
- Add `evaluate` to `_HELP_COMMANDS`.

`_parse_evaluate_command` / `_render_evaluate` / `_render_evaluate_targets`
mirror the `_parse_optimize_command` family so they are unit-testable without
the REPL loop.

## Files touched

New:

- `src/ubongo/evolution/sandbox.py` — the offline harness: prompt assembly,
  generate + judge per sample, `CallBudget`, per-variant aggregation, the judge
  rubric + parser. Public: `evaluate_variant(...)`, `evaluate_target(...)`.
- `src/ubongo/evolution/fitness.py` — `compute_fitness(metrics, weights)`,
  cohort min-max normalization, deterministic `rank_evaluations(...)`.
- `tests/manual/fixtures/sample_conversations.json` — 30+ conversations.

Modified:

- `src/ubongo/memory/store.py` — `append_evaluation`, `evaluations_for_target`,
  `latest_evaluation_for_lineage`.
- `src/ubongo/repl.py` — `/evaluate` dispatch, parser, renderers, help string.
- `src/ubongo/config.py` — reuse `load_evolution()`; read `fitness_weights`,
  `max_calls_per_hour`, and the new `samples_per_eval`.
- `config/settings.yaml` — add `evolution.samples_per_eval: 5` (the per-run
  sample cap; the only config change this phase).

No schema migration. `EvaluatorAgent`, governance, runner, master untouched.

## Tests

Unit (`tests/`), all LLM calls monkeypatched:

- `test_evolution_fitness.py` — weighted sum math; cohort normalization;
  single-variant degenerate case; deterministic tiebreak (equal fitness → lower
  lineage_id first); weights load from config.
- `test_evolution_sandbox.py` — `evaluate_variant` aggregates quality →
  success_rate, hallucination mean, correction fraction, cost/latency means; the
  judge parser tolerates code fences and rejects malformed JSON; `CallBudget`
  stops at the cap and yields partial results; a deliberately "bad" judged
  response drives `hallucination_rate` up (scenario 3); prompt assembly uses
  UBONGO.md + variant_text and no skill/memory layers.
- `test_evolution_evaluations.py` (store) — `append_evaluation` round-trips;
  `evaluations_for_target` joins lineage + orders by fitness desc, lineage asc;
  generation filter.
- `test_repl_evaluate.py` — `/evaluate` lists targets; `/evaluate
  persona:architect` after a seeded generation writes rows and renders a
  fitness-sorted leaderboard; budget exhaustion prints a partial-results note
  (scenario 2); unknown target errors; `evaluate` in help.

Spec scenario coverage:

| # | Scenario | Covered by |
| --- | --- | --- |
| 1 | `/evaluate` after Phase 16 generation → leaderboard with fitness | `test_repl_evaluate` |
| 2 | Cost cap respected → throttles, partial results | `test_evolution_sandbox` + `test_repl_evaluate` |
| 3 | Hallucination signal on a bad variant | `test_evolution_sandbox` |
| 4 | Deterministic tiebreak on near-equal variants | `test_evolution_fitness` |

## Smoke additions

Append a Phase 17 section to `tests/manual/smoke_test.md`: an
optimize-then-evaluate cycle for one persona —

- `/optimize persona:casual` (Phase 16) seeds a generation.
- `/evaluate persona:casual` prints a fitness leaderboard; the rows persist to
  `evolution_evaluations` (verify with a `sqlite3` count).
- Set `max_calls_per_hour: 10` in a scratch config and confirm `/evaluate`
  throttles with a partial-results note.
- Confirm the leaderboard order is stable across two runs on identical inputs
  (determinism).

## Out of scope (later phases)

- The autonomous GP loop, cross-run hourly rate windows, generations-per-run,
  `cron` → Phase 18.
- Promotions, `/improvements`, `pending_promotions` / `active_evolutions`
  writes, live-target swap → Phase 19.
- Evolvable routing / tool-chains / retry strategies (only persona targets are
  evaluable here, inherited from Phase 16's registry) → Phase 19.

## Branch workflow

1. `git switch -c phase-17-evaluation` off `main` at `175c9ff`.
2. First commit `Plan: Phase 17 — Sandboxed Evaluation + Fitness` (this doc),
   push, open a **draft** PR titled `Phase 17 — Sandboxed Evaluation + Fitness`,
   base `main`, body linking this plan.
3. Implement in order: 17a fixture → 17b sandbox → 17c fitness → 17d evaluation
   writes → 17e `/evaluate` → 17f safeguards, tests alongside each.
4. Run the full pytest suite (currently 549 green; Phase 17 adds ~25–30) and the
   Phase 17 smoke section. Live-LLM smoke (the actual leaderboard quality) needs
   `OPENROUTER_API_KEY`, like Phase 16.
5. Mark the PR ready; user reviews, user merges. Do not start Phase 18 until this
   merges.

## Acceptance

- All four spec scenarios pass; smoke section passes; full pytest green.
- `/evaluate <target>` and `/evaluate` (list) work; `evolution_evaluations`
  populated; leaderboard deterministic and fitness-ranked.
- Call budget throttles and returns partial results; no schema migration;
  `EvaluatorAgent` / governance unchanged.
- LOC stays well under the 15k soft target.
