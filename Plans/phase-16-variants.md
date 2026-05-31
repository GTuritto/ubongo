# Phase 16 — Variant Generation: Implementation Plan

Date: 2026-05-31
Branch: `phase-16-variants` (off `main` at `3989628`)
Spec source: [UBONGO_BUILD.md](../UBONGO_BUILD.md) §Phase 16 (lines 1166–1192).
Tier: 5 — Self-Improvement (first phase).

## Context

Tiers 1–4 are complete and merged: the full runtime classifies, plans, dispatches a
fleet across six execution modes, self-heals failures, and gates risk through an
approval matrix with a sandboxed executor. What is missing is the engine the whole
v0.1 vision is named for — genetic-programming self-improvement.

Phase 16 is the first step and deliberately the smallest: it generates prompt
**variants** on demand. No evaluation (Phase 17), no fitness (Phase 17), no
autonomous loop (Phase 18), no promotions (Phase 19). A user runs `/optimize
persona:architect` and gets N alternate prompts, each persisted to a lineage row.

Two things are already in place and must not be rebuilt:

- **Schema.** [schema.sql](../src/ubongo/memory/schema.sql#L98) already defines
  `evolution_lineage` (`id, target, parent_id, generation, variant_text,
  variant_metadata JSON, created_at`) plus the downstream `evolution_evaluations`,
  `pending_promotions`, `active_evolutions` tables and the
  `idx_lineage_target_gen` index. **No migration is needed.** Phase 16 only writes
  to `evolution_lineage`; the other three stay empty until Tiers 17/19.
- **Config.** [settings.yaml](../config/settings.yaml) already ships an
  `evolution:` block (`enabled`, `population_size: 8`, `generations_per_run`,
  `max_calls_per_hour`, `fitness_weights`) and a `models.evolution_generator`
  entry. Phase 16 reads `population_size` and `models.evolution_generator`; the
  rest (`max_calls_per_hour`, `generations_per_run`, `fitness_weights`, `cron`)
  belongs to Phases 17/18 and stays untouched.

## Goal

`/optimize <target>` generates `population_size` (8) strategy-diverse prompt
variants for an evolvable target, persists each as a generation-N
`evolution_lineage` row, and prints them. `/optimize` with no argument lists the
evolvable targets. That is the entire phase.

## What "target" means in Phase 16

The only evolvable targets are the three persona prompts, addressed as
`persona:architect`, `persona:operator`, `persona:casual`. A target string
resolves to its **base text** — the prompt the variants mutate from.

- The base is the persona file *body* (the markdown after frontmatter) loaded via
  the existing `agents.personas` loader, e.g. [architect.md](../config/personas/architect.md).
- Later phases will swap the base to a promoted lineage row when
  `active_evolutions[target]` exists. Phase 16 anticipates this seam but, since no
  promotions exist yet, the base is always the config file and the lineage
  ancestor is `NULL` (see parent_id below).

The registry is intentionally small and explicit (no auto-discovery of arbitrary
prompts). Routing rules, tool chains, and retry strategies become evolvable
targets in Phase 19; Phase 16 scopes to personas only, matching the spec's
testing scenarios (`persona:architect`, `persona:casual`).

## Mutation strategies (`evolution/generator.py`)

Five strategies, four LLM-driven and one pure metadata:

| Strategy | Kind | What it does |
| --- | --- | --- |
| `paraphrase` | LLM | Rewrite the prompt preserving meaning and constraints; change surface form only. |
| `prune` | LLM | Remove the least load-bearing sentences; tighten to a shorter, denser prompt. |
| `expand` | LLM | Add specificity — concrete instructions, edge-case handling — without changing the role. |
| `recombine` | LLM | Blend the base with a *peer* target's prompt (e.g. architect × operator), keeping the base's role dominant. |
| `perturb_temperature` | metadata | `variant_text` == base text verbatim; `variant_metadata` records a sampling-temperature delta. No LLM call. |

Design notes:

- **Diversity (scenario 2).** `generate(target, n)` allocates the `n` variants by
  round-robin over the strategy list, so 8 variants = paraphrase, prune, expand,
  recombine, perturb_temperature, paraphrase, prune, expand. Never "all
  paraphrases."
- **`recombine` needs a peer.** It picks another persona target as the second
  parent (architect↔operator, casual→operator). If a target has no configured
  peer, `recombine` is skipped and the round-robin advances to the next strategy,
  so the variant count is always met.
- **`perturb_temperature` is cheap and deterministic** — it is the one strategy
  that produces a variant without an LLM call, which keeps a full `/optimize` run
  to ~6–7 generator calls. Its temperature deltas come from a fixed sequence
  (e.g. +0.2, −0.2) so successive perturb variants differ; `Math.random` is not
  used (and is unavailable in this environment anyway).
- **Generator model** is `models.evolution_generator` from settings. Each LLM
  strategy is one `llm.complete` call with a strategy-specific system prompt and
  the base text as the user message. Failures degrade gracefully: a strategy that
  raises `LLMError` is logged and dropped, and the run continues (a short run of
  fewer than 8 is acceptable and surfaced to the user, not a crash).
- **Rate limiting is deferred.** `evolution.max_calls_per_hour` is a Phase 18
  (autonomous loop) concern. Phase 16 generates on explicit user command only, so
  no throttle is wired, but the generator is structured as a single `generate()`
  entry point so a Phase 18 rate guard can wrap it without refactoring.

## Persistence (`evolution/lineage.py` + store accessors)

House rule: raw SQL lives in `memory/store.py`; domain logic lives in its module.
So:

- **`store.append_lineage_variant(target, parent_id, generation, variant_text,
  variant_metadata) -> int`** — one INSERT into `evolution_lineage`, returns the
  new row id. Mirrors the existing `append_workflow_run` / `append_repair_run`
  style.
- **`store.lineage_for_target(target, generation=None) -> list[dict]`** — reader
  for tests, the REPL, and Phase 17. Filtered by target, optionally by generation,
  ordered by id.
- **`store.active_lineage_id(target) -> int | None`** — reads
  `active_evolutions[target].lineage_id`; returns `None` when no promotion exists
  (always `None` in Phase 16). This is the parent pointer source.
- **`evolution/lineage.py`** holds a `Variant` dataclass (`strategy`, `text`,
  `metadata`) and `record_variants(target, variants) -> list[int]`: it computes
  the next generation via `max(existing generation for target) + 1` (or `1` when
  none — satisfies scenario 1's `generation=1`), resolves `parent_id` from
  `store.active_lineage_id(target)` (NULL in Phase 16, scenario 3), and calls
  `store.append_lineage_variant` per variant. `variant_metadata` records
  `{strategy, base_source, ...}` so Phase 17 can see provenance.

This keeps the architectural seam clean: `evolution/` decides *what* to write,
`store.py` performs the write, and the persona Memory Agent path is untouched
(evolution lineage is infra/audit data, like `workflow_runs`, written directly via
`store` — not user-facing durable memory).

## REPL command (`/optimize`)

Add an `if head == "optimize":` branch to the slash dispatch in
[repl.py](../src/ubongo/repl.py#L505), alongside `/exec`, `/mode`, `/skill`:

- `/optimize` (no arg) → print the evolvable targets from the registry
  (scenario 4).
- `/optimize persona:architect` → resolve base, `generate(target, population_size)`,
  `record_variants(...)`, then print each variant: index, strategy, a short text
  preview, and the lineage row id. Print the count actually produced (e.g.
  "8 variants, generation 1, target=persona:architect").
- `/optimize <unknown>` → `Unknown target: <x>. Evolvable targets: …`.
- Add `optimize` to `_HELP_COMMANDS` and the `--help`/usage strings.

A small `_parse_optimize_command` + `_render_*` pair mirrors the existing
`_parse_mode_command` / `_render_mode_list` helpers so it is testable without the
REPL loop. No `master.handle` involvement — `/optimize` is a direct tool like
`/exec` (no `workflow_runs`, no governance, no enqueue).

## Files touched

New:

- `src/ubongo/evolution/__init__.py`
- `src/ubongo/evolution/generator.py` — strategies + `generate(target, n)`.
- `src/ubongo/evolution/targets.py` — registry (`evolvable_targets()`,
  `resolve_base(target)`, `peer_of(target)`), persona-only in Phase 16.
- `src/ubongo/evolution/lineage.py` — `Variant`, `record_variants`,
  generation/parent resolution.

Modified:

- `src/ubongo/memory/store.py` — `append_lineage_variant`, `lineage_for_target`,
  `active_lineage_id`.
- `src/ubongo/repl.py` — `/optimize` dispatch, parser, renderer, help string.
- `src/ubongo/config.py` — small cached `load_evolution()` accessor (mirrors
  `load_governance()`), or read `load_config()["evolution"]` directly if simpler.
- `config/settings.yaml` — no change expected; the `evolution:` block already
  exists. (Touch only if a per-strategy knob proves necessary.)

No schema migration. No new governance/runner/master code.

## Tests

Unit (`tests/`):

- `test_evolution_targets.py` — `evolvable_targets()` returns the three
  `persona:*`; `resolve_base` returns the persona body; unknown target raises;
  `peer_of` mapping.
- `test_evolution_generator.py` — `generate` returns exactly `n` variants;
  strategy diversity (not all one strategy); `perturb_temperature` produces
  `variant_text == base` with a temperature delta in metadata and makes **no** LLM
  call; `recombine` skipped-and-backfilled when no peer; LLM strategies use the
  `evolution_generator` model; an `LLMError` in one strategy drops that variant
  without aborting the run. `llm.complete` is monkeypatched.
- `test_evolution_lineage.py` — `record_variants` writes N rows with
  `generation=1` on first run, `2` on second; `parent_id` NULL when no active
  promotion; metadata round-trips; `store.append_lineage_variant` /
  `lineage_for_target` / `active_lineage_id` behave.
- `test_repl_optimize.py` — `/optimize` lists targets; `/optimize persona:architect`
  prints 8 variants and writes 8 lineage rows; `/optimize bogus` errors;
  `optimize` appears in help.

Targets the spec testing table directly:

| # | Scenario | Covered by |
| --- | --- | --- |
| 1 | `/optimize persona:architect` → 8 variants, 8 gen-1 rows | `test_repl_optimize` + `test_evolution_lineage` |
| 2 | Strategy diversity (not all paraphrases) | `test_evolution_generator` |
| 3 | `parent_id` points to current active (NULL in Phase 16) | `test_evolution_lineage` |
| 4 | `/optimize` no args lists targets | `test_repl_optimize` |

## Smoke additions

Append a Phase 16 section to `tests/manual/smoke_test.md`:

- `/optimize` with no args lists `persona:architect`, `persona:operator`,
  `persona:casual`.
- `/optimize persona:casual` produces 8 plausible alternate prompts; the printed
  preview reads as casual-voice variants, not gibberish.
- A follow-up DB check (or `lineage_for_target`) shows 8 rows, `generation=1`,
  `target=persona:casual`.
- Running it a second time yields `generation=2`.

## Out of scope (later phases, do not build)

- Evaluation, fitness scoring, sample-set runs → Phase 17.
- The autonomous GP loop, throttling, cron, `generations_per_run` → Phase 18.
- Promotions, `/improvements`, `pending_promotions` / `active_evolutions` writes,
  live-target swap, evolvable routing/tool-chains → Phase 19.

## Branch workflow

1. `git switch -c phase-16-variants` off `main` at `3989628`.
2. First commit `Plan: Phase 16 — Variant Generation` (this doc), push, open a
   **draft** PR titled `Phase 16 — Variant Generation`, base `main`, body linking
   this plan.
3. Implement sub-phases in order: 16a generator → 16b targets → 16c lineage
   persistence → 16d `/optimize` REPL command, with tests alongside each.
4. Run the full pytest suite (currently 515 green; Phase 16 adds ~20–25) and the
   Phase 16 smoke section.
5. Mark the PR ready; user reviews, user merges. Do not start Phase 17 until this
   merges.

## Acceptance

- All four spec scenarios pass; smoke section passes; full pytest green.
- `evolution/` package created; `/optimize <target>` and `/optimize` (list) work.
- No schema migration; `evolution_lineage` populated, other evolution tables
  untouched.
- LOC stays well under the 15k soft target (Phase 16 is a few hundred lines).
