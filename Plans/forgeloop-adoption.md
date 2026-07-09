# ForgeLoop adoption — the workflow standard (docs-only)

Status: **APPROVED 2026-07-09 (recommendations 1–3 as stated), implemented.** Docs-only phase, branch `docs/forgeloop-adoption` (no
version bump — the branch does not match the `v0.X/NN` release pattern, same as the logo PRs).
Part 1 of a two-part sequence; part 2 (Ubongo as the executable ForgeLoop harness) is drafted
separately in [forgeloop-harness.md](forgeloop-harness.md) and is not sequenced by this plan.

Work classification: maintenance. Rigor mode: Docs-only.

Source: the ForgeLoop repo at `/Volumes/giuseppeM1mini-External/Coding/ForgeLoop` — a docs-first
workflow standard (compact `FORGELOOP_CORE.md` spine, full reference in
`AI-Assisted-Development-Workflow.md`, an eleven-template pack, its own roadmap and ADR 0001).
ForgeLoop's own instruction: *adapt the minimum useful parts into the target repo; do not copy
the full reference document.*

## The one-sentence claim

Ubongo already runs ~90% of the ForgeLoop workflow under its own names; this phase adopts the
missing 10% (a tool-agnostic operating spine, work classification, rigor modes, a docs index)
and records the mapping in an ADR, so any agent — Claude Code, Codex, or a future Ubongo forge —
loads one standard instead of re-deriving the process from `CLAUDE.md` prose.

## The reconciliation map

What ForgeLoop asks for versus what Ubongo already has. "Native" means keep Ubongo's artifact
and record the equivalence; "gap" means this phase adds it.

| ForgeLoop concept | Ubongo today | Verdict |
| --- | --- | --- |
| Core spine loaded by agents (`FORGELOOP_CORE.md` → `AGENTS.md`/`CLAUDE.md`) | `CLAUDE.md` (Claude-specific, includes repo status prose) | **Gap**: no tool-agnostic `AGENTS.md` |
| Source-of-truth order (repo evidence before chat memory) | Implicit in CLAUDE.md ("living state docs are the freshest truth") | **Gap**: not stated as an ordered rule |
| Core loop (Idea → Docs → Decisions → Roadmap → Spec → Plan → Branch → Tests → Code → Smoke → PR → Merge) | The Plans/ + branch-per-phase + draft-PR + smoke loop | Native — same loop, house-named |
| Work classification (greenfield / brownfield / maintenance) | Not classified per phase | **Gap** |
| Execution modes (Docs-only, Mechanical, Low-risk, Standard, Strict, Release-critical) | Not declared per phase | **Gap** — adopted as **rigor modes** (see collision note) |
| Tool modes (Single-tool / Multi-tool / Human-plus-tool) | Implicit (Claude builds, Giuseppe reviews and merges) | **Gap**: one line in the spine |
| Project tier selector | N/A | **Gap**: declare once |
| Roadmap / Master Plan | `Plans/v0.X-*.md` lines + `PROJECT_STATUS.md` position | Native |
| Phase plan + approval gate | `Plans/` doc, draft PR, explicit user approval | Native |
| ADRs | `docs/adr/` (0000–0022 accepted) | Native |
| Behavior specs | Plan QA sections + acceptance criteria | Native (per-plan, not a separate artifact) |
| QA / manual / integration test plans | Per-plan QA sections + `tests/manual/smoke_test.md` | Native |
| Execution report / PR description templates | The house PR-body convention (what + why + testing) | Native |
| Builder / critic role split (ForgeLoop ADR 0001) | Claude builds, Giuseppe critiques and merges; in-runtime: Coding vs Evaluator/Critic agents | Native |
| Docs navigation index (`docs/00-index.md`) | None — the doc estate is large and unindexed | **Gap** |

### The name collision, resolved up front

ForgeLoop's "execution modes" are ceremony tiers for development work. Ubongo's "execution
modes" are the six runtime dispatch modes of the agent platform (`chat_simple`,
`research_session`, ... `connector_session`). Same words, unrelated concepts, one repo. The
adoption renames the ForgeLoop concept to **rigor modes** everywhere in Ubongo's docs, with the
mapping recorded in `CONTEXT.md` and the ADR. The ForgeLoop repo keeps its own vocabulary; the
adaptation owns the rename.

## What changes (sub-phases)

1. **`AGENTS.md` at the repo root — the adapted spine.** The ForgeLoop Core, Ubongo-shaped and
   tool-agnostic: the source-of-truth order (Ubongo-ized: code/tests/git state, then the active
   plan, then ADRs, then `PROJECT_*` briefings, then README, then chat memory), the core loop
   named in house terms, work classification, the six **rigor modes**, tool modes, the
   non-negotiables (plan before code, stop at gates, one sub-phase at a time, commit/PR rule).
   Repo-specific rules are **linked, not duplicated**: branch workflow, LOC budget, and
   architectural rules stay canonical in `CLAUDE.md` (ForgeLoop's own "canonical concept home"
   principle). Target: under ~80 lines.
2. **`docs/00-index.md` — the documentation map.** Default agent load order
   (`AGENTS.md` → `CONTEXT.md` → the index → the active plan), then the canonical homes:
   briefings (`PROJECT_*`), plans (`Plans/`), decisions (`docs/adr/`), glossary, smoke playbook,
   agent skills (`docs/agents/`), architecture docs, historical docs (`UBONGO_BUILD.md`,
   `STATUS.md`/`STATE.md`) marked as such.
3. **`CONTEXT.md` — glossary additions.** `ForgeLoop`, `rigor mode` (with the collision note),
   `work classification`, `tool mode`, `execution report`. Small, additive.
4. **ADR-0023 — adopt ForgeLoop as the development workflow standard.** Records: what was
   adopted (spine, classification, rigor modes, index), what stayed native (plans, ADRs, QA
   shape, PR discipline — with the mapping table), the rename decision, the declared tier, and
   that the template pack is referenced at its source rather than vendored. Supersedes nothing.
5. **`CLAUDE.md` — one short pointer section.** "Development workflow: ForgeLoop" — three or four
   lines linking `AGENTS.md` and the ADR, plus declaring each new phase states its work
   classification and rigor mode in its plan header. No other `CLAUDE.md` restructuring (the
   file carries uncommitted refresh edits; see risks).
6. **Tier and defaults, declared once (in `AGENTS.md`).** Ubongo is a `Real project` run at
   strict discipline: trust-spine work (governance, sandbox, egress, approvals, grants) is
   always `Strict` or `Release-critical` rigor; docs like this phase are `Docs-only`.

Not in scope: vendoring the eleven ForgeLoop templates (Ubongo's plan/QA shape already satisfies
them; the ADR records the pointer to `ForgeLoop/docs/templates/` for future artifact types),
any change to runtime code, any change to the v0.6 line, any harness work (that is
[forgeloop-harness.md](forgeloop-harness.md)).

## QA plan

Work classification: maintenance. Rigor mode: Docs-only (this plan eats its own dog food).

### Acceptance criteria (exit = all checked)

- [x] **AC-1** `AGENTS.md` exists, under ~80 lines, tool-agnostic, and contains no rule that
      contradicts `CLAUDE.md`; overlapping concepts link to their canonical home instead of
      restating it.
- [x] **AC-2** `docs/00-index.md` maps every top-level doc and marks historical docs as
      historical; every relative link in `AGENTS.md` and the index resolves.
- [x] **AC-3** `CONTEXT.md` defines the adopted terms and the rigor-mode rename; the term
      "execution mode" remains unambiguous everywhere it appears.
- [x] **AC-4** ADR-0023 is accepted, carries the full reconciliation map, and records what was
      deliberately *not* adopted.
- [x] **AC-5** The cold-load test passes: an agent given only `AGENTS.md` → `CONTEXT.md` →
      `docs/00-index.md` can state the active plan line, the current phase, and the approval
      gate rules without reading `UBONGO_BUILD.md` or the full ForgeLoop reference.
- [x] **AC-6** No runtime file changed; the pytest suite is untouched and green.

### Smoke addition

A short docs-integrity section appended to `tests/manual/smoke_test.md`: run a link check over
`AGENTS.md` and `docs/00-index.md` (a small Python one-liner resolving relative links), then
perform the AC-5 cold-load test in a fresh session and record the answer.

## Risks and coordination

- The worktree currently carries **uncommitted refresh edits** to `CLAUDE.md` and the
  `PROJECT_*` briefings, plus the untracked `Plans/pluggable-execution-backend.md`. This plan
  does not touch them; the `CLAUDE.md` pointer section (sub-phase 5) must be written against
  whichever state lands first. Cleanest order: commit the refresh first, then implement this
  phase.
- Over-adoption is the real failure mode: ForgeLoop itself warns against forcing ceremony. The
  spine must stay a loading layer, not a second rulebook — AC-1's "no duplication" check is the
  guard.

## Open decisions (need your call at approval)

1. **Spine placement.** `AGENTS.md` as the tool-agnostic spine with `CLAUDE.md` linking to it
   (recommended — Codex and future tools read `AGENTS.md` natively), or fold the spine into
   `CLAUDE.md` and skip the new file.
2. **The rename.** "Rigor mode" for ForgeLoop's ceremony tiers (recommended), or another name
   ("ceremony tier", "process mode"), or keep ForgeLoop's "execution mode" and disambiguate by
   context (not recommended — the collision is real).
3. **Retroactive labels.** Whether to backfill work classification + rigor mode onto the two
   live draft plans (`pluggable-execution-backend.md`, `forgeloop-harness.md`) as part of this
   phase, or only require the header for plans written after adoption.
