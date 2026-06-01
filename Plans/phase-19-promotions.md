# Phase 19 — GP Targets Expanded + Promotions: Implementation Plan

Date: 2026-06-01
Branch: `phase-19-promotions` (off `main` at `40fe1fa`)
Spec source: [UBONGO_BUILD.md](../UBONGO_BUILD.md) §Phase 19 (lines 1255–1285).
Tier: 5 — Self-Improvement (final phase). **End of Tier 5.**

> This is the largest phase in Tier 5: it closes the self-improvement loop
> (promotions + live swap) AND generalizes the evolution machinery from
> prompt-shaped targets to config-shaped ones. The sub-phases are sequenced so
> the loop-closing value (personas) lands first and is independently testable
> before the config-target expansion.

## Context

Phases 16–18 built generation, evaluation, and the autonomous loop — but no
variant is ever *promoted*. The loop evolves and ranks; nothing acts on the
winners. Phase 19 closes that: the loop proposes a promotion when a champion
beats the active baseline, the user approves/rejects/rolls back via
`/improvements`, and approval performs a **live swap** so the promoted variant
actually changes behavior. It also expands evolvable targets beyond persona
prompts to routing rules, per-workflow tool chains, and the Repair retry config.

Already in place (reused):

- `pending_promotions` and `active_evolutions` tables (in `schema.sql` since
  Phase 16; still empty). `store.active_lineage_id(target)` reads
  `active_evolutions`.
- `targets.resolve_base` already checks `active_evolutions` first — but only the
  **generator** calls it. The live persona agent uses
  `context.build_system_prompt`, which reads the persona *file*. **Live swap
  therefore requires wiring the runtime read paths to consult promotions** (the
  central new integration, see "Live swap").
- The generator (5 prompt strategies, budget/parent params), the evaluation
  sandbox (`evaluate_target`, the 3-signal judge, `CallBudget`), `fitness`, and
  the loop (`run_one_cycle`, `EvolutionLoop`).

## Decisions locked with the user

- **Full target expansion.** Routing rules, per-workflow tool chains, and the
  Repair retry config all become evolvable, each promotable and live-swappable —
  not just personas.
- **Automatic promotion proposer.** When a cycle's champion survivor beats the
  active baseline's fitness by `evolution.promotion_margin`, the loop enqueues a
  `pending_promotions` row. The user still approves; nothing promotes
  autonomously (the vision's "approved, not autonomous" rule).

## The core abstraction: target *kinds*

A target is no longer always a prompt. Introduce a kind:

| Kind | Targets | `variant_text` holds | Live read path |
| --- | --- | --- | --- |
| `prompt` | `persona:architect\|operator\|casual` | the persona body | `context.build_system_prompt` |
| `config` | `routing:default` | serialized routing rules (YAML) | `router.route_workflow` |
| `config` | `toolchain:<workflow>` | serialized agent list (YAML) | `router.workflow_agents` |
| `config` | `retry:repair` | serialized repair config (YAML) | `agents/repair.py` settings read |

`evolution_lineage.variant_text` is already `TEXT`, so a serialized config blob
fits with no schema change; `variant_metadata.kind` records the kind.

`targets.py` generalizes:

- `evolvable_targets()` returns persona + config targets. Config targets are an
  explicit, reviewable allowlist (e.g. `routing:default`, `toolchain:` for the
  auto-routed workflows, `retry:repair`) — no auto-discovery.
- `target_kind(target) -> "prompt" | "config"`.
- `resolve_base(target)` returns the promoted active variant when one exists,
  else: prompt → persona body; config → the serialized live config section.
- `apply_variant(target, variant_text)` parses + **validates** a config variant
  (well-formed YAML, structurally valid: routing rules reference real
  workflows; tool-chain agents exist in the registry; retry keys are known).
  Invalid variants are rejected (generation drops them; promotion refuses them).

## Generation: dispatch on kind (`generator.py`, 19a–c)

- **Prompt targets:** the existing 5 strategies, unchanged.
- **Config targets:** a config-mutation strategy set chosen by kind:
  - `routing` (19a): reorder rules, add/remove/retarget a `match → workflow`
    rule, change `default_workflow`. Plus an LLM-proposed variant ("here is the
    routing config; propose a small change that might route turns better; return
    valid YAML").
  - `toolchain` (19b): for a workflow's agent list, swap/add/remove/reorder an
    agent (drawn from the registry), keeping a composer present.
  - `retry` (19c): mutate `peer_replacements` / ladder knobs.
  - Every produced config variant is parsed + validated via
    `targets.apply_variant`; invalid ones are dropped (the run never persists a
    malformed config). Budget-gated exactly like Phase 18.

## Evaluation: one fitness, two harnesses (`sandbox.py`)

Fitness stays the cohort-normalized weighted sum over the same five signals; the
difference is how responses are produced:

- **Prompt targets:** `evaluate_target` (Phase 17) — swap the persona body,
  generate a response per sample, judge.
- **Config targets:** new `evaluate_config_variant` — run the **real turn
  pipeline** (classify → route → execute) under an *in-memory config override*
  carrying the variant, on a small sample subset, with **no side effects**
  (a throwaway context: no `workflow_runs` / `agent_runs` / governance / vault /
  queue / memory writes), then judge each produced response with the same
  3-signal judge. This keeps fitness uniform across kinds.
  - **Config-override seam:** the router caches routing/workflows at module
    level. Add an explicit override (a context manager
    `router.config_override(routing=…, workflows=…)` that swaps the caches and
    restores them) so an evaluation run routes/executes under the variant
    without mutating files or leaking into production. The same seam powers live
    swap (below) by reading the promoted config instead.
  - **Cost:** config eval runs *full turns* (classify + multi-agent workflow),
    far pricier than a single generation. It is bounded hard by a small
    `samples_per_eval` and the shared `CallBudget`; the plan caps config cohorts
    aggressively and `log()`s what was skipped.
- **`retry:repair` is the weakest link, flagged honestly.** Retry quality only
  manifests under failure, which offline samples don't induce. v0.1 scores it
  with a **structural proxy** (the ladder/peer-map is well-formed and covers the
  failure kinds; no regression on a normal turn) rather than response-quality,
  and the plan documents this limitation. A fault-injection harness is a
  follow-up.

## Promotion proposer (automatic, in the loop) — 19d

After `run_one_cycle` ranks a cohort:

- Compute the **baseline fitness**: the active variant's latest evaluation, or a
  one-time baseline evaluation of the file default if unpromoted.
- If `champion.fitness >= baseline.fitness + evolution.promotion_margin`
  (new config key, default `0.05`) and there is no open `pending_promotions`
  row for the target, call `store.append_pending_promotion(target, lineage_id)`
  and emit the `evolution_promotion` event.
- The user approves; nothing auto-applies.

## Promotion flow + store (`evolution/promotion.py`, repl) — 19e/f

Store accessors:

- `append_pending_promotion(target, lineage_id) -> int`
- `open_pending_promotions() -> list[dict]` (undecided; joined to lineage for
  text + fitness)
- `decide_promotion(id, decision)` (`approved` | `rejected`, stamps `decided_at`)
- `set_active_evolution(target, lineage_id)` / `clear_active_evolution(target)`
  / `active_evolution(target)`

`promotion.py` orchestrates approve (decide + `set_active_evolution` + audit +
cache-bust), reject (decide + audit), rollback (`clear_active_evolution` +
audit + cache-bust).

REPL `/improvements`:

- `/improvements` — list open pending promotions: target, fitness delta
  (champion − baseline), and a **diff** of the variant vs the current base
  (a unified text diff for prompts; a YAML diff for configs).
- `/improvements approve <id>` — promote (live swap takes effect immediately).
- `/improvements reject <id>` — record; queue shrinks.
- `/improvements rollback <target>` — revert to the file/default; live swap off.
- Add `improvements` to `_HELP_COMMANDS`.

## Live swap — the load-bearing integration

Approval must change runtime behavior (spec scenario 4). Each read path consults
`active_evolutions`:

- **persona:** `context.build_system_prompt(persona)` checks
  `store.active_lineage_id("persona:<p>")`; if set, uses the promoted
  `variant_text` as the body instead of the file (frontmatter — model/max_tokens
  — still comes from the file).
- **routing:** `router.route_workflow` reads the promoted `routing:default`
  rules when present (via the override seam, populated from `active_evolutions`).
- **toolchain:** `router.workflow_agents` reads the promoted agent list for a
  promoted `toolchain:<wf>`.
- **retry:** `agents/repair.py` reads the promoted `retry:repair` config.
- On approve/rollback, bust the relevant caches (`router.reload()`,
  `context.reload()`, `personas.reload()`) so the swap is immediate within the
  running REPL.

## Audit log — 19g

`vault/system/evolution-audit.md`: one appended row per promotion decision
(timestamp, target, lineage_id, action ∈ approve/reject/rollback, fitness delta,
baseline→champion). A dedicated `vault.append_audit(...)` writer (not a daily
note). Created on first decision.

## Files touched

- `src/ubongo/evolution/targets.py` — kinds, config targets, `resolve_base`
  generalization, `apply_variant` validation.
- `src/ubongo/evolution/generator.py` — config-mutation strategies (dispatch on
  kind).
- `src/ubongo/evolution/sandbox.py` — `evaluate_config_variant` + the config
  override usage; `retry` structural proxy.
- `src/ubongo/evolution/promotion.py` (new) — proposer helper, approve/reject/
  rollback orchestration, audit.
- `src/ubongo/evolution/loop.py` — call the proposer after ranking a cohort.
- `src/ubongo/router.py` — `config_override` context manager + live-swap reads.
- `src/ubongo/context.py` — persona live-swap read.
- `src/ubongo/agents/repair.py` — promoted retry-config read.
- `src/ubongo/memory/store.py` — pending/active promotion accessors.
- `src/ubongo/memory/vault.py` — `append_audit`.
- `src/ubongo/repl.py` — `/improvements` command + help.
- `config/settings.yaml` — `evolution.promotion_margin: 0.05`.
- `vault/system/evolution-audit.md` — created on first decision (gitignored like
  the rest of `vault/`).

No schema migration (`pending_promotions` / `active_evolutions` already ship).

## Tests

Unit (`tests/`), no real LLM:

- `test_evolution_targets_kinds.py` — kind classification; config `resolve_base`
  serializes the live section; `apply_variant` accepts valid / rejects malformed
  (bad YAML, unknown workflow, unknown agent).
- `test_evolution_generator_config.py` — config strategies produce *valid*
  variants; invalid mutations dropped; budget-gated.
- `test_evolution_config_eval.py` — `evaluate_config_variant` runs the pipeline
  under an override with zero side effects (no `workflow_runs`/vault/queue rows),
  judges responses, returns metrics; the override restores cleanly.
- `test_evolution_promotion.py` — proposer enqueues only when champion beats
  baseline + margin and no open row exists; approve writes `active_evolutions` +
  audit; reject records; rollback clears; `decide_promotion` idempotency.
- `test_live_swap.py` — with an active persona promotion, `build_system_prompt`
  uses the promoted body; with an active routing promotion, `route_workflow`
  uses promoted rules; rollback reverts. (cache-bust verified)
- `test_repl_improvements.py` — `/improvements` list with diff + delta; approve/
  reject/rollback parse + render; `improvements` in help.
- Extend `test_evolution_loop_cycle.py` — a cycle whose champion beats baseline
  enqueues a pending promotion.

Spec scenario coverage:

| # | Scenario | Covered by |
| --- | --- | --- |
| 1 | Routing-rule variant → diff + fitness delta | `test_evolution_generator_config` + `test_repl_improvements` |
| 2 | Approve → `active_evolutions` + audit row | `test_evolution_promotion` |
| 3 | Reject → recorded, queue shrinks | `test_evolution_promotion` |
| 4 | Live swap → new rules in effect | `test_live_swap` |
| 5 | Rollback reverts cleanly | `test_evolution_promotion` + `test_live_swap` |

## Smoke additions

Append a Phase 19 section to `tests/manual/smoke_test.md`: with evolution
enabled and resumed, let the loop run until `/improvements` is non-empty; show a
prompt diff and a routing diff with fitness deltas; `approve` a persona
promotion and confirm a normally-classified turn reflects the new prompt
(`build_system_prompt` swap); `approve` a routing promotion and confirm a turn
routes to the new workflow (scenario 4); `reject` one (queue shrinks); `rollback`
a target and confirm revert; check `vault/system/evolution-audit.md` has a row
per decision. Live steps need `OPENROUTER_API_KEY`; the control logic is
unit-covered.

## Sequencing (sub-phases land in this order on the branch)

1. **STATUS.md flip** (Phase 18 → Complete, the deferred handoff fix) + this
   plan as the first commit; draft PR.
2. **Promotion store + flow + persona live swap + audit** (19d/e/f/g for
   personas) — closes the loop, independently testable, the highest-value slice.
3. **Target kinds + config `resolve_base`/`apply_variant`** (the abstraction).
4. **Config generation** (19a/b/c strategies) + validation.
5. **Config evaluation harness** (`evaluate_config_variant` + router override) +
   config live swap.
6. **Retry structural proxy** (flagged-weak) + loop proposer wiring for all
   kinds.
7. Smoke + full-suite + a bounded live run (loop → propose → approve → observe
   swap).

If the config-target half proves larger than one PR can carry cleanly, slices
3–6 can land as a clearly-marked second batch on the same branch; slice 2 still
delivers the loop-closing value on its own.

## Out of scope

- A fault-injection harness for true retry-strategy fitness (structural proxy
  ships; real harness is a follow-up).
- Tier 6: embeddings + graph (Phase 20), bidirectional vault sync (Phase 21).

## Branch workflow

1. `git switch -c phase-19-promotions` off `main` at `40fe1fa`.
2. First commit: this plan + STATUS.md flip; push; open a **draft** PR titled
   `Phase 19 — GP Targets Expanded + Promotions`, base `main`, linking the plan.
3. Implement in the sub-phase order above, tests alongside each.
4. Full pytest (currently 623; Phase 19 adds ~40–50) + the Phase 19 smoke
   section + a bounded live run.
5. Mark ready; user reviews, user merges. **End of Tier 5** — Phase 20 starts
   Tier 6 only after this merges.

## Acceptance

- All five spec scenarios pass; smoke passes; full pytest green.
- The loop auto-proposes; `/improvements` approve/reject/rollback work; approval
  performs a live swap for personas AND configs; audit log appended per decision.
- Routing rules, tool chains, and retry config are evolvable (retry via a
  documented structural proxy); no schema migration; Phases 16–18 behavior
  intact for prompt targets.
- LOC stays under the ~15k soft target.
