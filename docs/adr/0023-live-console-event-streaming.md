# 0023 — The live console: per-turn event streaming over the bus

Status: Accepted
Date: 2026-06-20

## Context

Opening the v0.6 live-console line. The goal is a browser front that shows a turn
*as it runs* — the agent roster lighting up, the pipeline stepping, the answer
arriving — the experience of a modern coding-agent UI, but over Ubongo's planned
multi-agent turn. Almost every panel that experience needs is data Ubongo already
produces: the roster is the `WorkflowPlan`, the steps are the named bus events,
history is the trace, sources are recall. The one thing missing is **liveness**: a
turn runs synchronously and returns once, and the event bus is in-process and
synchronous. Phase 00 builds exactly that missing primitive; the rich panels are
later phases.

## Decision

A sixth channel, additive over `channel.run_turn` — the MCP/Telegram pattern. The
new part is per-turn event streaming:

- **`web/console/stream_bridge.py`** is the channel-free, HTTP-free core. `start_turn`
  registers handlers on the pipeline events (`after_classify`, `after_plan`,
  `agent_started/completed/failed`, `after_govern`, `after_compose`, `after_send`),
  runs `channel.run_turn` on a **background thread**, and forwards a small
  JSON-safe summary of each event into the session's queue; `event_stream` drains
  the queue as SSE frames until a terminal `__end__`. This is the CLAUDE.md rule
  made literal — new behavior is a handler on the named events, not a pipeline edit.
- **Single-flight (D1).** One active turn at a time; a second `start_turn` is
  refused. The console serves one user at one keyboard, and — crucially — the
  console server starts no daemons (`channel.bootstrap` never does), so the only
  events that fire during a turn are that turn's own. No correlation plumbing; a
  `contextvar`-tagged variant is a later phase if concurrent turns are ever wanted.
- **The turn is observation only.** The stream reads the bus and reports; it never
  touches orchestration. No bypass of classify → plan → execute → govern → compose
  → enqueue (ADR-0002/0003).
- **`web/console/app.py`** is the only module importing FastAPI/uvicorn (the
  optional `[console]` extra, imported lazily in `run`). `POST /turn` →
  `start_turn`; `GET /stream/{id}` → an SSE response whose every blocking queue
  read is offloaded to a worker thread so the event loop never blocks; `GET /` →
  a bare `console.html` event log. `ubongo console` is the entrypoint; ctl +
  start script mirror the other channels.
- **Token-streaming the answer is deferred (D2).** This phase streams pipeline
  *events*, not answer tokens — matching what the reference UIs actually show.
  Token streaming would touch `llm.complete` (the one model chokepoint) and is a
  later, flag-gated phase.

## Consequences

- The hard primitive lands once and cheaply: the roster/activity/approval/sources
  panels (later phases) are all readers over data that already exists, riding this
  transport. Phase 00 is the only phase that adds a genuinely new mechanism.
- **No auth, no TLS** — LAN-only by design, like the web and MCP channels. The
  egress envelope (ADR-0017) is the network boundary; a console exposed beyond the
  LAN would need auth, which is out of scope. Recorded, not hidden.
- A background-turn exception can never hang the stream: the driver always emits a
  terminal frame and always unregisters its handlers (a `finally`), so the browser
  always sees the turn end and the next turn starts clean.
- Single-flight is a real limitation (no two concurrent console turns) accepted for
  v1 simplicity; it is also what lets the bridge skip event correlation entirely.
- The `[console]` extra keeps FastAPI/uvicorn out of the core install and the test
  suite; the bridge is unit-tested directly, no HTTP.
- This is the first phase of a new line that is *not* the trust protocol — a UI
  want, deliberately sequenced after v0.5 closed.
