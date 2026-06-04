# 0001 — Hand-rolled orchestration (no graph/workflow framework)

Status: Accepted
Date: backfilled 2026-06-04 (decision dates to Tier 2, Phases 8–12)

## Context

Ubongo dispatches a fleet of worker agents across multiple execution modes, with retries, governance, and a background self-improvement loop. The obvious reach is for an orchestration framework — LangGraph, Temporal, Ray, or an actor system — to model the agent graph, retries, and concurrency. v0.1 is a single-user, single-process, local CLU with a hard simplicity goal (~15k LOC) and an explicit "not a distributed system" scope (no Docker, Kubernetes, Temporal, Redis).

## Decision

Hand-roll the orchestration in plain Python: a `MasterAgent.handle` pipeline (classify → plan → execute → govern → compose → enqueue), a `WorkflowRunner` whose six modes are `asyncio` strategy coroutines, and an in-process event bus (`events.py`) for side effects. No graph/workflow/queue framework. Concurrency inside the runner is plain `asyncio`; the public boundaries stay synchronous.

## Consequences

- Full control and zero framework lock-in or operational surface; the whole control flow is readable in a handful of modules.
- We own what a framework would give us: retry/repair logic (`runner` + Repair Agent), the rolling-hour throttle for the GP loop, and thread-safety for the shared SQLite connection (`check_same_thread=False`). The Phase-12a parallel-store-read flakiness is a direct cost of owning concurrency ourselves.
- v0.2+ behavior is added as event handlers on named events rather than by re-wiring a graph, which keeps additions additive.

References: `UBONGO_BUILD.md` Tier 2; `CLAUDE.md` ("Hand-rolled orchestration"); `src/ubongo/{master,runner,events}.py`.
