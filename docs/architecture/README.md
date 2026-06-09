# Ubongo Architecture (C4)

Architecture documentation for Ubongo using the [C4 model](https://c4model.com/).
Diagrams are Mermaid C4; GitHub and most Markdown viewers render them inline.

Reflects **v0.1 complete** — all 22 phases (0–21) merged to `main`. All six
tiers are done: Foundation, Multi-Agent, Self-Healing, Governance,
Self-Improvement (the closed GP loop with human-approved promotions), and Wiki
Memory + Polish (sqlite-vec semantic recall, the vault-link graph, and
bidirectional vault sync). See [STATUS.md](../../STATUS.md) for the changelog.

Updated 2026-06-07 for the post-v0.1 architecture-deepening refactors
([ADR-0012](../adr/0012-agent-envelope-directives-and-router-planning.md)): the
shared **model-call envelope** (`agents/llm_run`), **typed `AgentDirectives`**, and
**router-owned `plan_workflow`**. These are internal refactors — the container and
context boxes are unchanged; the orchestration component and dynamic-turn diagrams
reflect the new planning seam and envelope.

## Diagrams

| Level | File | Audience | Shows |
|-------|------|----------|-------|
| 1 — Context | [c4-context.md](c4-context.md) | Everyone | Ubongo, the user, and external systems |
| 2 — Container | [c4-containers.md](c4-containers.md) | Technical | Internal modules and their responsibilities |
| 3 — Component | [c4-components-orchestration.md](c4-components-orchestration.md) | Developers | Master Agent + Workflow Runner internals |
| 3 — Component | [c4-components-memory.md](c4-components-memory.md) | Developers | Memory subsystem internals (incl. embeddings, graph, vault watcher) |
| 3 — Component | [c4-components-evolution.md](c4-components-evolution.md) | Developers | GP self-improvement loop internals |
| 3 — Component | [c4-components-authoring.md](c4-components-authoring.md) | Developers | Self-authored skills (post-v0.1): draft → quarantine → approve |
| Dynamic | [c4-dynamic-turn.md](c4-dynamic-turn.md) | Technical | One user message, end to end |

## Reading order

Start with **Context** for scope, then **Container** for the module map. The
three **Component** diagrams (orchestration, memory, evolution) and the
**Dynamic** turn trace add depth where it helps; they are not needed to
understand the whole.

## Scope

These diagrams document the complete v0.1 system, all six tiers. The
turn-orchestration core (Context, Container, the orchestration and memory
component diagrams, the dynamic turn trace) is joined by the two Tier-5/6
background daemons the REPL starts alongside the synchronous turn loop:

- the **GP self-improvement loop** (`src/ubongo/evolution/`) — generation,
  sandboxed evaluation + fitness, the throttled/pausable `EvolutionLoop`, and
  human-approved promotions with live swap, across persona prompts and
  routing/tool-chain/retry config — drawn in
  [c4-components-evolution.md](c4-components-evolution.md);
- the **wiki-memory** features — `sqlite-vec` semantic recall, the vault-link
  graph, and the `VaultWatcher` bidirectional-sync daemon with a unified audit
  log — now drawn into [c4-components-memory.md](c4-components-memory.md) and the
  container diagram;
- the post-v0.1 **self-authored skills** experiment (`src/ubongo/authoring/`,
  [ADR-0013](../adr/0013-self-authored-skills-quarantine-and-approval.md)) — manual
  `/author`, the `/skill-candidates` approval gate, and a third background daemon,
  the paused-by-default `AuthoringLoop` — drawn in
  [c4-components-authoring.md](c4-components-authoring.md).

The build specification in [UBONGO_BUILD.md](../../UBONGO_BUILD.md) remains the
source of truth for v0.1 scope.
