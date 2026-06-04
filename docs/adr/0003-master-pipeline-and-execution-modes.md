# 0003 — Master Agent pipeline + six execution modes

Status: Accepted
Date: backfilled 2026-06-04 (decision dates to Phases 8, 10, 12)

## Context

A turn needs to be classified, routed to the right persona/workflow, executed by one or more agents, gated for risk, composed into a single reply, and delivered. Different tasks want different agent-collaboration shapes: a quick single-persona answer, a fan-out for breadth, a contest for quality, a debate for contested calls, a cheap-then-verify for latency.

## Decision

- A single orchestration seam: `MasterAgent.handle` runs **classify → plan → execute → govern → compose → enqueue** with no bypass paths. REPL and one-shot both delegate to it.
- The `WorkflowRunner` supports **six execution modes** selected off `workflow.execution_mode`: `sequential`, `parallel`, `competitive`, `collaborative`, `debate`, `speculative`. Each mode is an `asyncio` strategy coroutine; the runner is async internally but `execute()` stays synchronous, so master/REPL stay sync.
- The **Composer** rule decides the user-facing text: `WorkflowResult.text` is the last agent with `composer = True`, so validators (Evaluator/Critic) and helpers (Research/Execution) can run without claiming the response.
- Only `sequential` and `parallel` auto-route; the other four are opt-in per turn via `/mode`.

## Consequences

- Uniform persistence and governance for every turn regardless of mode.
- The composer attribute decouples "who runs last" from "whose text wins," which the GP loop later reuses.
- Repair behaves differently per mode (full ladder in sequential, peer-replacement in fan-out), an accepted asymmetry because cancel-and-retry under `asyncio.gather` is ambiguous.

References: `UBONGO_BUILD.md` Phases 8/10/12; `src/ubongo/{master,runner}.py`; `CLAUDE.md` (Composer attribute).
