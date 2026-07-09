# 0023 — Adopt ForgeLoop as the development workflow standard

Status: Accepted
Date: 2026-07-09

## Context

Ubongo grew a docs-first, phase-gated development process organically across v0.1 and v0.5:
plans in `Plans/`, ADRs, a branch-per-phase workflow with draft PRs, per-phase QA plans, and a
cumulative smoke playbook. The process lived as prose in `CLAUDE.md` — Claude-specific, mixed
with repo status, and re-derived by every new tool or session.

ForgeLoop (the repo at `/Volumes/giuseppeM1mini-External/Coding/ForgeLoop`) formalizes the same
workflow as a reusable standard: a compact loadable spine (`FORGELOOP_CORE.md`), a full
reference (`AI-Assisted-Development-Workflow.md`), and an eleven-template pack. Its own
instruction is to adapt the minimum useful parts into a target repo, never to copy the full
reference. A comparison showed Ubongo already runs roughly 90% of the standard under house
names; the plan behind this ADR ([Plans/forgeloop-adoption.md](../../Plans/forgeloop-adoption.md))
carries the full reconciliation map.

## Decision

Adopt ForgeLoop as Ubongo's development workflow standard, as an adaptation, not a copy:

1. **The spine is a new tool-agnostic [AGENTS.md](../../AGENTS.md)** at the repo root: source-
   of-truth order, the core loop in house terms, work classification, rigor modes, tool modes,
   the non-negotiables, and the declared tier. Repo-specific rules (branch workflow,
   architectural rules, LOC budget) stay canonical in `CLAUDE.md` and are linked, not restated —
   ForgeLoop's own canonical-concept-home principle.
2. **ForgeLoop's "execution modes" are renamed to "rigor modes"** in Ubongo. In this repo,
   *execution mode* already names the WorkflowRunner's six dispatch strategies; one repo cannot
   carry two meanings for the word. The adaptation owns the rename; the glossary records it.
3. **Every new plan header states its work classification and rigor mode.** Trust-spine work is
   `Strict` minimum. The two live draft plans are backfilled; historical plans are records and
   stay untouched.
4. **A documentation map exists at [docs/00-index.md](../00-index.md)** with the default agent
   load order and the historical docs marked as such.
5. **Native artifacts are kept where Ubongo's shape already satisfies the standard**: `Plans/`
   docs are the roadmap and phase plans; per-plan QA sections and acceptance criteria are the
   behavior specs and test plans; the PR body plus checked criteria plus the smoke section are
   the execution report; `docs/adr/` is the decision log; the builder/critic role split
   (ForgeLoop ADR 0001) is the existing Claude-builds / Giuseppe-critiques split, mirrored in
   the runtime by Coding versus Evaluator/Critic.
6. **The template pack is referenced at its source, not vendored.** Ubongo's plan and QA shapes
   already satisfy the templates; copying eleven files would add drift surface for zero new
   capability. Future artifact types start from `ForgeLoop/docs/templates/`.
7. **Declared tier: `Real project` at strict discipline** — single-user and local, but the
   governance, sandbox, and egress seams get production-grade care.

### The reconciliation map

| ForgeLoop artifact | Ubongo home |
| --- | --- |
| Core spine (`FORGELOOP_CORE.md`) | `AGENTS.md` (new) |
| Source-of-truth order | `AGENTS.md` (new) |
| Work classification + execution modes | Plan headers: classification + **rigor modes** (new) |
| Tool modes | `AGENTS.md` (new) |
| Docs navigation index | `docs/00-index.md` (new) |
| Roadmap / Master Plan | `Plans/` lines + `PROJECT_STATUS.md` (kept) |
| Phase plan + approval gate | `Plans/` doc, draft PR, explicit user approval (kept) |
| Behavior specs, QA / manual / integration test plans | Per-plan QA sections + `tests/manual/smoke_test.md` (kept) |
| Execution report + PR description | PR body + checked criteria + smoke section (kept) |
| ADRs | `docs/adr/` (kept) |
| Builder / critic roles (ForgeLoop ADR 0001) | Agent builds / Giuseppe critiques; runtime mirror: Coding vs Evaluator/Critic (kept) |
| Template pack | Referenced at the source, not vendored |

Out of scope here: any runtime change. Ubongo as ForgeLoop's *executable harness* (a
`forge_session` mode working target repos through the governed seams) is a separate draft line,
[Plans/forgeloop-harness.md](../../Plans/forgeloop-harness.md), unsequenced and unapproved.

## Consequences

- Any agent — Claude Code, Codex, or a future Ubongo forge — loads one standard
  (`AGENTS.md` → `CONTEXT.md` → `docs/00-index.md` → the active plan) instead of re-deriving
  the process from `CLAUDE.md` prose. `CLAUDE.md` gains a pointer section and loses nothing.
- Plan headers now carry classification and rigor, making ceremony an explicit, reviewable
  choice instead of an instinct. The cost is two header lines per plan.
- The rename closes the execution-mode ambiguity permanently; the glossary is the tiebreaker.
- Adopting the standard without vendoring its templates means ForgeLoop's pack can evolve
  without churning this repo; the trade is that template improvements arrive only when a plan
  reaches for them.
- If the harness line is ever sequenced, it enforces this adopted standard rather than a
  private variant — the standard and the harness cannot drift apart.
