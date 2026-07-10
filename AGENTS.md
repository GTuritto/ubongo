# Ubongo — Agent Operating Spine

The workflow standard for any coding agent working on this repo (Claude Code, Codex, or another
tool), adapted from ForgeLoop ([ADR-FL-0001](docs/adr/FL-0001-adopt-forgeloop-workflow-standard.md)
records the adoption and the full mapping). Repo-specific rules stay canonical in
[CLAUDE.md](CLAUDE.md) and are linked, not restated.

Default load order:

1. This file.
2. [CONTEXT.md](CONTEXT.md) — the domain glossary.
3. [docs/00-index.md](docs/00-index.md) — the documentation map.
4. The active plan in [Plans/](Plans/) — find the position via
   [PROJECT_STATUS.md](PROJECT_STATUS.md).

## Source of Truth

Use repository evidence before chat memory, in this order:

1. Current code, tests, and git state.
2. The approved plan for the active phase ([Plans/](Plans/)).
3. Accepted decisions ([docs/adr/](docs/adr/)).
4. The living briefings: [PROJECT_STATUS.md](PROJECT_STATUS.md),
   [PROJECT_STATE.md](PROJECT_STATE.md), [PROJECT_ARCHITECTURE.md](PROJECT_ARCHITECTURE.md).
5. [README.md](README.md) and the historical docs (marked historical in the index).
6. Agent chat history or memory — last, and never against the above.

## The Core Loop

Idea → plan doc in `Plans/` (with QA criteria and a smoke section) → explicit user approval →
branch → draft PR → implement one sub-phase at a time → tests + smoke → PR ready → the user
merges. Branch naming, draft-PR timing, version bump, and merge rules are canonical in
[CLAUDE.md](CLAUDE.md#branch-workflow).

## Classify Before You Work

Every new plan states two things in its header:

- **Work classification** — `greenfield` (a new capability), `brownfield` (existing behavior to
  protect), or `maintenance` (repair, cleanup, docs, dependencies).
- **Rigor mode** — how much ceremony and verification the work gets:
  - `Docs-only`: prose and templates; verification is link and consistency checking.
  - `Mechanical`: rote, reversible edits; the existing suite must stay green.
  - `Low-risk`: narrow code changes; unit tests for the touched seam.
  - `Standard`: a normal phase; full plan, QA criteria, smoke addition.
  - `Strict`: touches a governed seam; adversarial review plus explicit rollback notes.
  - `Release-critical`: trust-spine or data-migration work; everything above plus a manual
    live check before merge.

"Rigor mode" is ForgeLoop's "execution mode", renamed: in Ubongo, *execution mode* means the
WorkflowRunner's six dispatch strategies (see [CONTEXT.md](CONTEXT.md)). Rigor floors, not
ceilings: work touching the trust spine (governance, sandbox, egress, approvals, grants,
self-modification) is `Strict` minimum. Escalate when in doubt; de-escalate only when repo
evidence proves the change is narrow and reversible.

## Tool Modes

- `Single-tool`: one agent plans, builds, verifies, and documents in separate passes.
- `Multi-tool`: one tool builds, another critiques.
- `Human-plus-tool` (Ubongo's default): the agent builds; Giuseppe reviews, approves gates,
  runs manual QA, and merges.

## Non-Negotiables

- Confirm the work has a home in a current `Plans/` phase or an accepted ADR before building;
  otherwise it is out of scope.
- Prepare or update the plan before implementation; stop for approval at the gate. Approval is
  explicit — answers to open questions alone are not a green light.
- Implement one sub-phase at a time, green at HEAD.
- Run the phase's testing plan and append its smoke section to
  [tests/manual/smoke_test.md](tests/manual/smoke_test.md) before marking the PR ready.
- When behavior changes, update the glossary, diagrams, ADRs, and briefings as one coordinated
  update.
- Commits and PRs explain both what changed and why. Commit and push only on explicit ask;
  never merge to `main` yourself.

## Project Tier

Ubongo is a ForgeLoop `Real project` run at strict discipline: single-user and local, but its
governance, sandbox, and egress seams get production-grade care.
