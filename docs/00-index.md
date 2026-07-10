# Ubongo Documentation Index

The navigation map for the repo's documentation. Default agent load order:
[AGENTS.md](../AGENTS.md) → [CONTEXT.md](../CONTEXT.md) → this index → the active plan.

## Start Here (living, regenerated)

- [PROJECT_STATUS.md](../PROJECT_STATUS.md) — fast catch-up: plan position, what works, what's
  missing.
- [PROJECT_STATE.md](../PROJECT_STATE.md) — full state for "what to build next" strategy.
- [PROJECT_ARCHITECTURE.md](../PROJECT_ARCHITECTURE.md) — component map, seams, decisions, the
  core invariant.
- [CLAUDE.md](../CLAUDE.md) — the repo rulebook: architectural rules, branch workflow, LOC
  budget, scope guardrails.
- [AGENTS.md](../AGENTS.md) — the tool-agnostic operating spine (ForgeLoop, ADR-FL-0001).
- [CONTEXT.md](../CONTEXT.md) — the domain glossary.
- [README.md](../README.md) — the public entrypoint.

## Plans

One doc per phase or line under [Plans/](../Plans/); the active line and position are in
[PROJECT_STATUS.md](../PROJECT_STATUS.md).

- Active line: [Plans/v0.6-live-console.md](../Plans/v0.6-live-console.md).
- Prior lines: [Plans/v0.5-trust-protocol.md](../Plans/v0.5-trust-protocol.md) (complete),
  the v0.1 per-phase plans (historical).
- Draft, unsequenced: [Plans/pluggable-execution-backend.md](../Plans/pluggable-execution-backend.md),
  [Plans/forgeloop-harness.md](../Plans/forgeloop-harness.md).

## Decisions

- [docs/adr/](adr/) — the accepted ADRs (0000 onward), narratively indexed in
  [docs/adr/README.md](adr/README.md). Supersede, don't rewrite.

## Testing

- [tests/manual/smoke_test.md](../tests/manual/smoke_test.md) — the cumulative manual smoke
  playbook, grown phase by phase.
- `tests/manual/fixtures/` — held-out conversation samples for evolution evaluation.
- Pytest: roughly one module per source module; run `uv run pytest`.

## Agent Skills and Conventions

- [docs/agents/issue-tracker.md](agents/issue-tracker.md) — GitHub issues via `gh`.
- [docs/agents/triage-labels.md](agents/triage-labels.md) — the five triage roles.
- [docs/agents/domain.md](agents/domain.md) — single-context domain docs.
- [config/UBONGO.md](../config/UBONGO.md) — user communication preferences.

## Architecture

- [docs/system-architecture.md](system-architecture.md) — the prose overview.
- [docs/architecture/](architecture/) — C4 diagrams (context, containers, per-subsystem
  components, the dynamic turn) and agent notes.
- [docs/diagrams/](diagrams/) — the draw.io source.
- [docs/SECURITY.md](SECURITY.md), [docs/USER_MANUAL.md](USER_MANUAL.md),
  [docs/ubongo-open-items.md](ubongo-open-items.md).
- [docs/architecture-review-2026-06-05.md](architecture-review-2026-06-05.md) — a point-in-time
  review (historical).

## Historical (not current state)

- [UBONGO_BUILD.md](../UBONGO_BUILD.md) — the v0.1 build spec; source of truth for v0.1 scope
  only.
- [UBONGO_VISION.md](../UBONGO_VISION.md) — the conceptual origin.
- [STATUS.md](../STATUS.md) / [STATE.md](../STATE.md) — the v0.1-era changelog, last current at
  v0.1.5.
- [CHANGELOG.md](../CHANGELOG.md) — release notes.

## The ForgeLoop Source

The adopted workflow standard ([ADR-FL-0001](adr/FL-0001-adopt-forgeloop-workflow-standard.md)) comes
from the ForgeLoop repo at `/Volumes/giuseppeM1mini-External/Coding/ForgeLoop`: the compact
`FORGELOOP_CORE.md`, the full reference `AI-Assisted-Development-Workflow.md`, and the template
pack at `docs/templates/`. Templates are referenced at the source, not vendored; Ubongo's own
plan and QA shapes already satisfy them.
