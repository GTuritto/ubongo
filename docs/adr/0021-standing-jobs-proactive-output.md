# 0021 — Standing jobs: proactive output through the existing seams

Status: Accepted
Date: 2026-06-20

## Context

Through Phase 05, every turn Ubongo ran was *reactive* — a human typed something.
The trust protocol's Phase 06 is the first time it speaks **unprompted**: a
scheduled job composes a turn on its own and delivers the result. The
architecture reserved the seam for this from v0.1 — the notification queue exists
so that "proactive jobs inherit it without restructuring" (ADR-0002) — but it had
never been used.

The hard part is not scheduling; it is safety with **no human present at run
time**. A proactive turn can gate (`require_approval`), and there may be nobody
to answer. A naive design either blocks forever, fires at 3am, or — worst —
lets a job quietly widen what it touches.

Four prior seams make this additive rather than structural: the shared
`DaemonLoop` lifecycle (the GP/authoring loops, the vault watcher), the
notification queue (ADR-0002), the resumable approval record (ADR-0018), and the
grant registry (ADR-0019).

## Decision

Standing jobs ship as a fourth daemon plus a small state module and a proactive
policy. Nothing about orchestration changes.

- **`StandingJobsLoop`** is a fourth `DaemonLoop`, booting **paused** like its
  siblings (it never speaks unprompted until `/jobs resume`), throttled by the
  same rolling-hour budget + cron gate. `UBONGO_DISABLE_JOBS=1` keeps the suite
  daemon-free.
- **A job's turn goes through `master.handle`** (`jobs/runner.py`): classified,
  governed, persisted, exactly like a typed turn. The runner never composes or
  delivers around the seam.
- **Config-defined jobs** (`config/jobs.yaml`) — `name`, `schedule_seconds`,
  `grant_bundle`, `persona`, optional `workflow`, `prompt`. D1: no live `/jobs
  add` CRUD of scheduled side effects; `/jobs` is read + control only. Runtime
  state (last/next run) lives in `standing_jobs`; per-cycle verdicts in
  `job_runs`; the control row in `jobs_state`.
- **Definition-time grant bundle.** A job declares its `connector:<server>`
  classes; the first run gates once through the Phase-03 seam, approving writes
  the Phase-05 grants, and later runs proceed within the bundle. A run reaching
  outside the bundle re-gates → park-and-raise.
- **Park-and-raise.** On `require_approval` the job parks (master already wrote
  the `pending_approvals` record) and enqueues a *raise* — a proactive message
  naming the `decision_id`. Approving later (phone/REPL/CLI, ADR-0018) delivers
  on the next cycle via the existing resume path; no new resume logic.
- **The approval-expiry posture (D2 — the safety core).** Two controls, both
  enforced by the queue's existing deliverability filter, computed at enqueue in
  `jobs/policy.py`: **quiet hours** set a future `deliver_after` so a send inside
  the window is *held* and surfaces as a catch-up; a raise's **TTL** sets
  `expires_at`, and the loop's sweep **auto-declines** an expired raise
  (default-deny) so the job retries on its next schedule rather than piling up.
  This is the "no human at 3am" answer the state briefing said Phase 06 could not
  ship without. (We chose enqueue-time policy over a `before_send` handler: the
  queue already filters on `deliver_after`/`expires_at`, so the policy is a pure
  time computation, not a new delivery hook.)
- **External reach is the Connector** (D3), not a shell script — the sandbox
  blocks network by design (ADR-0005/0016). The news-digest example routes
  through `connector_session`; its bundle is `connector:news`.
- **Proactive transport is the queue + a drain** (D4): a job's user-facing output
  is a distinct `source` (`proactive` / `proactive-raise`) from a typed turn's
  `response` row. Whatever channel is listening drains it — the REPL as a launch
  catch-up, the Telegram bot each poll. ADR-0002 is intact: proactive output
  still flows through `notification_queue`; the drain is just the reader.

## Consequences

- **Proactive output is governed by construction.** A job cannot deliver an
  ungoverned message: every job turn passes `master.handle`, and the only
  delivery path is the queue. The grant bundle never overrides the safety rules
  (rule 1 still wins), so a destructive job gates regardless of its grant.
- **No unbounded backlog and no 3am pings.** Quiet hours hold; TTL auto-declines.
  A parked job makes forward progress only with explicit approval, and forgets
  the ask if it goes stale.
- **Two queue rows per delivered job** (the turn's own `response` artifact, then
  the `proactive` notification). They serve different roles — the turn record vs
  the decoupled push — which is the cost of separating the turn from its audience.
- **Telegram/REPL gain proactive delivery with a few lines each** (a drain call);
  no new transport. Live proactive Telegram still needs a token + the bot running,
  like every live external check (Pi-only).
- **The LOC budget grows.** This is a subsystem, not a transport: a daemon, a
  state module, a policy, a runner, a command pack. It reuses the four prior seams
  wholesale, but it is the heaviest Phase since the MCP halves. Budget honesty is
  noted; the offsetting cut is deferred to Phase 07's contract work.
