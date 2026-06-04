# 0002 — Single-writer durable memory; everything through the queue

Status: Accepted
Date: backfilled 2026-06-04 (decision dates to Phases 4–9)

## Context

Many agents run per turn and several could plausibly write durable state (messages, summaries, facts, vault notes, embeddings). Concurrent or scattered writes invite duplicate assistant turns, partial state on failure, and races on the shared SQLite connection. Separately, outbound delivery (CLI now, Telegram in v0.2, proactive jobs in v0.3) needs one consistent path with before/after hooks.

## Decision

- **The Memory Agent is the only writer** to durable memory. Other agents return Findings; the Memory Agent commits. Raw SQL lives in `memory/store.py`; audit/infra rows (workflow_runs, agent_runs, governance_decisions, repair_runs, evolution_*) are written directly through `store` by the runner/master, but user-facing durable memory goes through the Memory Agent.
- **Every outbound message passes through `notification_queue`**, even synchronous CLI responses — enqueue, fire `before_send`, return a token; the caller prints, then `flush_delivered` fires `after_send` (vault projection) and marks delivered.
- Commit-on-success: `master.handle` stages the assistant-message commit in a `workflow_buffer` and commits on `result.ok` or drops on failure, so a half-finished turn leaves no partial rows.

## Consequences

- One coherent write path; tracing is not optional (every run/decision/variant persisted).
- The queue indirection is required even for the simplest reply, which is slightly more ceremony but makes v0.2 transports and v0.3 proactive jobs additive.
- Tests must respect the single-writer rule (a strict-mode fixture blocks non-Memory assistant writes).

References: `CLAUDE.md` (Architectural Rules); `src/ubongo/agents/memory.py`, `memory/store.py`, `memory/write_buffer.py`, `delivery/queue.py`.
