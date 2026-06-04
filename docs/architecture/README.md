# Ubongo Architecture (C4)

Architecture documentation for Ubongo using the [C4 model](https://c4model.com/).
Diagrams are Mermaid C4; GitHub and most Markdown viewers render them inline.

Reflects **v0.1 complete** — all 22 phases (0–21) merged to `main`. All six
tiers are done: Foundation, Multi-Agent, Self-Healing, Governance,
Self-Improvement (the closed GP loop with human-approved promotions), and Wiki
Memory + Polish (sqlite-vec semantic recall, the vault-link graph, and
bidirectional vault sync). See [STATUS.md](../../STATUS.md) for the changelog.

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

These diagrams document the complete v0.1 system. The full GP self-improvement
layer is built (`src/ubongo/evolution/`): generation, sandboxed evaluation +
fitness, the autonomous `EvolutionLoop` daemon, and human-approved promotions
with live swap, across persona prompts and routing/tool-chain/retry config. So
are the wiki-memory features: `sqlite-vec` semantic recall, the vault-link
graph, and the `VaultWatcher` bidirectional-sync daemon with a unified audit
log. The component/container diagrams below predate Tiers 5–6 and focus on the
turn-orchestration core; the evolution and vault-sync daemons are background
threads the REPL starts alongside it. The build specification in
[UBONGO_BUILD.md](../../UBONGO_BUILD.md) remains the source of truth for v0.1
scope.
