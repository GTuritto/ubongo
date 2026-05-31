# Ubongo Architecture (C4)

Architecture documentation for Ubongo using the [C4 model](https://c4model.com/).
Diagrams are Mermaid C4; GitHub and most Markdown viewers render them inline.

Reflects the codebase as of Phase 16 (Phases 0-15 merged to `main`; Phase 16,
variant generation, merged via PR #8) — Tiers 1-4 complete and Tier 5
(Self-Improvement) opened. See [STATUS.md](../../STATUS.md) for current progress.

## Diagrams

| Level | File | Audience | Shows |
|-------|------|----------|-------|
| 1 — Context | [c4-context.md](c4-context.md) | Everyone | Ubongo, the user, and external systems |
| 2 — Container | [c4-containers.md](c4-containers.md) | Technical | Internal modules and their responsibilities |
| 3 — Component | [c4-components-orchestration.md](c4-components-orchestration.md) | Developers | Master Agent + Workflow Runner internals |
| 3 — Component | [c4-components-memory.md](c4-components-memory.md) | Developers | Memory subsystem internals |
| Dynamic | [c4-dynamic-turn.md](c4-dynamic-turn.md) | Technical | One user message, end to end |

## Reading order

Start with **Context** for scope, then **Container** for the module map. The two
**Component** diagrams and the **Dynamic** turn trace add depth where it helps;
they are not needed to understand the whole.

## Scope

These diagrams document what is built. Phase 16 lit up the first piece of the
GP self-improvement layer: `/optimize <target>` generates prompt variants and
writes `evolution_lineage` rows (the `src/ubongo/evolution/` package). The rest
of the loop — evaluation/fitness, the autonomous scheduler, and promotions
(Phases 17-19) — and the wiki-memory features (Phases 20-21) are noted where
their seams already exist (the remaining `evolution_*` tables, the Event Bus
extension points) but are not yet drawn as live containers. The build specification in
[UBONGO_BUILD.md](../../UBONGO_BUILD.md) remains the source of truth for v0.1
scope.
