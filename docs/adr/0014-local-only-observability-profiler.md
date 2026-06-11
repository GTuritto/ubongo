# 0014 — Local-only observability: an in-process profiler over the run tables, no telemetry export

Status: Accepted
Date: 2026-06-11

## Context

Debugging and fixing issues in Ubongo needs three kinds of profiling: where the
time and tokens go per agent/model/execution mode (performance), where the time
goes inside the Python process (CPU), and what grows across a long-lived session
(memory). v0.1 already records the raw performance data — every turn persists
`workflow_runs.started_at/ended_at` and per-agent `latency_ms` / tokens /
outcomes — but nothing aggregated it, and CPU/memory were uncovered.

Three architectures were considered:

1. **An in-process, stdlib-only profiler** reading the existing SQLite run
   tables on demand, with opt-in cProfile / tracemalloc wraps around the turn.
2. **Push telemetry** (an OTLP exporter on the `after_llm` / `after_execute`
   events) into a local Grafana/collector stack running in Docker.
3. **A hosted observability service** (Better Stack-style) fed by a log shipper.

## Decision

Option 1. `ubongo.profiling` (shipped as v0.1.3, candidates 10–12):

- The **stats half** runs read-only SELECTs over `workflow_runs` / `agent_runs`
  at `/profile` time. No new tables, no event handlers, no second write path —
  the single-writer rule (ADR-0002) is untouched because the durable record
  **is already the export**.
- The **CPU half** (`/profile cpu on`, `ubongo send --profile`) wraps the turn's
  `master.handle` in stdlib `cProfile` inside try/finally; artifacts land in
  `data/profiles/*.prof`. The **memory half** (`/profile mem`) is a tracemalloc
  baseline-and-diff. Both are opt-in (zero overhead when off), best-effort (a
  profiling failure can never break a turn), and armed either per session or
  from boot via `--profile` / `UBONGO_PROFILE` (flag wins; invalid env never
  blocks startup).
- **No telemetry leaves the machine.** No exporter, no collector dependency, no
  hosted ingestion. Rationale: (a) Ubongo's telemetry sits next to — and in
  `agent_runs.input/output`, contains — personal conversation content; (b) a
  push pipeline duplicates facts SQLite already holds durably (two records that
  can disagree, and data lost whenever the collector is down — SQLite is the
  buffer); (c) an exporter today is one adapter at a hypothetical seam and fails
  the deletion test.
- Service control is **operational tooling around the unchanged runtime**:
  `ubongo-ctl.sh` (pidfile + log) and `deploy/ubongo-web.service` (systemd) are
  alternatives for backgrounding the existing web container. They add no new
  security boundary and no new code path inside Ubongo.

## Consequences

- Rich dashboards remain available with zero Ubongo changes: any external
  reader (e.g. Grafana with a SQLite datasource, or a snapshot pull over the
  LAN) can visualize the same tables retroactively, since history accumulates
  regardless of whether a viewer is running.
- A push exporter is deferred, not rejected forever: if v0.2 makes Ubongo a
  multi-process system (e.g. a Telegram transport as a separate long-lived
  process) and live cross-process traces are wanted, an OTLP handler on the
  named events becomes a real seam with a real second consumer — and per the
  event-bus rule it ships as an `after_*` handler, not a Master edit.
- cProfile sees the whole process including event-loop idle time; in an LLM
  orchestrator the dominant cost is network wait, which the stats half (not the
  CPU half) is the right tool for. This is documented and accepted.
- The `.prof` artifacts are analyzed locally (snakeviz / pstats); they are not
  ingestible by dashboard stacks and are not meant to be.
