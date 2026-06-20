# Changelog

Ubongo versioning is **v0.PLAN.PHASE** (pre-1.0):

- **PLAN** (the second number) names the *build plan*. `v0.1` is the original
  22-phase build (the multi-agent, self-improving CLI); its post-build increments
  walked `v0.1.1`…`v0.1.5` (web, skills, profiler, MCP server, MCP client). The
  current plan is the **trust-protocol plan, designated v0.5**
  ([Plans/v0.5-trust-protocol.md](Plans/v0.5-trust-protocol.md)) — so its releases
  are the `v0.5.x` line. The PLAN number is the plan's chosen name, not a
  sequential counter, which is why the series jumps `0.1.x → 0.5.x`.
- **PHASE** (the third number) is the phase within that plan. It matches the phase
  branch directly: branch `v0.5/02-store-split` ships as **`v0.5.2`**.

The current version is the single line in [`VERSION`](VERSION) (kept in sync with
`pyproject.toml`; the packaging script reads `VERSION` for the bundle name). Each
entry below records what that version added. Newest first.

---

## v0.5.7 — the contract and identity (v0.5 trust-protocol, phase 07)

Date: 2026-06-20

The final phase, deliberately light ([ADR-0022](docs/adr/0022-contract-and-identity.md)) —
and the close of the v0.5 trust protocol.

- **Verbosity per domain, as governance config.** A `verbosity:` block in `governance.yaml`
  maps a domain (classifier `task_type`, then `intent`) to `terse | normal | deep`. Resolved
  by `governance/verbosity.py`, set on the `Workflow`, passed as a typed
  `AgentDirectives.verbosity`, and appended by the composer persona as one length line
  (`normal` is a no-op — it never rewrites the voice). `/verbosity` shows the live table;
  `/brief` and `/verbose` override one turn. Manual-first; a GP-evolvable `verbosity:<domain>`
  target is deferred (it would stay human-approved, so the boundary can't move itself).
- **`ubongo backup` / `ubongo restore`.** An instance is its data + config: backup writes a
  portable `tar.gz` of `data/ubongo.db` + `vault/` + `config/`, never `.env` and never the
  disposable `data/profiles/`. Restore unpacks into a checkout and **re-arms grants** by
  default (a moved instance crosses a new trust boundary; `--keep-grants` for same-machine
  recovery). No install log — capabilities are the human-approved config allowlist.
- Fork / naming / inter-instance exchange recorded as designed-but-deferred (ADR-0022).
- Additive; every channel + orchestration unchanged (the verbosity directive degrades to
  normal when absent). 1053 tests green (`test_governance_verbosity.py` + `test_backup.py`).
- **The v0.5 trust protocol is complete** — phases 00–07 merged.

## v0.5.6 — standing jobs (v0.5 trust-protocol, phase 06)

Date: 2026-06-20

The first time Ubongo speaks unprompted ([ADR-0021](docs/adr/0021-standing-jobs-proactive-output.md)):
proactive output through the reserved seams. A fourth `DaemonLoop` runs scheduled
jobs through `master.handle` (governed, persisted, no bypass) and delivers via
`notification_queue`. Additive over the daemon lifecycle, the queue (ADR-0002),
the resumable approval seam (ADR-0018), and the grant registry (ADR-0019).

- `StandingJobsLoop` (a fourth daemon) boots **paused** like its siblings; nothing
  speaks unprompted until `/jobs resume`. Throttled by the shared rolling-hour
  budget + cron gate; `UBONGO_DISABLE_JOBS=1` keeps the suite daemon-free.
- Jobs are config-defined (`config/jobs.yaml`): name, schedule, grant_bundle,
  persona, optional workflow, prompt. Runtime state in `standing_jobs` / `job_runs`
  / `jobs_state` (a new table-family module, the Phase-02 pattern). `/jobs
  [status|list|pause|resume|off|run <name>]` + `ubongo jobs` are read + control only.
- **Definition-time grant bundle**: a job's `connector:<server>` classes are
  approved once through the Phase-03 seam; a run reaching outside the bundle
  re-gates → **park-and-raise** (the job parks, raises itself for approve-later;
  approving delivers on the next cycle, reusing the resume seam).
- **Approval-expiry posture** (the "no human at run time" safety core): quiet hours
  hold a send behind a future `deliver_after`; a parked raise's TTL `expires_at`
  auto-declines it (**default-deny**) so nothing waits forever or fires at 3am.
  Both enforced by the queue's existing deliverability filter.
- Proactive delivery is the queue + a drain (distinct `source`), read by the REPL
  as a launch catch-up and by the Telegram bot each poll. External reach is the
  Connector (the news-digest example, disabled by default). No new transport.
- Additive schema, no migration; the pipeline and every channel are unchanged for
  typed turns. 1039 tests green (`test_jobs_state.py` + `test_standing_jobs.py`).

## v0.5.5 — the grant registry (v0.5 trust-protocol, phase 05)

Date: 2026-06-13

The plan's one genuinely new subsystem ([ADR-0019](docs/adr/0019-grant-registry.md)):
persistent capability grants replace ask-every-time approval fatigue. Built
channel-free (it runs on the Phase-03 approval seam via the existing REPL/web/CLI
surfaces); the messaging channel is a later phase. The version sequence reads
`0.5.3 → 0.5.5` — `0.5.4` is reserved for the channel phase.

- The first connector turn touching a capability class (`connector:<server>`) with
  no active grant asks once through the approval seam; approving writes a grant;
  later turns in that class auto-proceed; revoking re-arms the ask (DB-backed,
  survives restart). The grant check is a new decision-matrix rule placed *after*
  the safety rules — a destructive connector turn still gates regardless of grants.
- Management: REPL `/grants` + `/grants revoke <id>`, CLI `ubongo grants [revoke <id>]`.
- Grants are server-granular (per-tool-name allowlists stay deferred until a real
  integration gives concrete tool names — ADR-0016).
- Paired cut (plan Amendment 2): the `retry:repair` evolvable target and its
  structural-proxy fitness are removed — the weakest GP signal (ADR-0007), the
  budget offset for the registry. Repair config stays human-edited.
- Behaviour-neutral for every non-connector turn; additive schema, no migration;
  single-writer rule untouched. 993 tests green.

## v0.5.4 — the Telegram channel (v0.5 trust-protocol, phase 04)

Date: 2026-06-13

The fifth channel and the first cloud-relayed one ([ADR-0020](docs/adr/0020-telegram-cloud-channel.md)):
drive Ubongo from your phone, and — the point — receive approve-later prompts and
grant first-encounter asks remotely. Fills the reserved v0.5.4 slot.

- `telegram/service.py` is the network-free, unit-testable core (auth + the
  `/approve|/decline|/pending|/grants` command router + turn handling); `telegram/bot.py`
  is the only module touching the Bot API (a thin httpx long-poll loop, lazy import,
  token from `TELEGRAM_BOT_TOKEN` in `.env`). `ubongo telegram` runs it.
- No bypass: every authorized message goes through `channel.run_turn` → `master.handle`.
  A gated turn surfaces the gated text and the decision_id; `/approve <id>` resolves it
  via the Phase-03 seam (no re-implemented resume).
- **Auth returns:** `telegram.allowed_user_ids` (empty = deny all, fail-closed). A minimal
  `before_send` policy seam (`delivery_paused`) is wired; the quiet-hours engine is later.
- Optional `[telegram]` extra (httpx), kept out of core; ctl + systemd unit +
  `start-ubongo-telegram.sh` + `install.sh --telegram`; `api.telegram.org` added to the
  egress allowlist (ADR-0017).
- Additive — REPL/one-shot/web/MCP and orchestration unchanged. 1010 tests green.

## v0.5.3 — the typed, resumable approval seam (v0.5 trust-protocol, phase 03)

Date: 2026-06-13

Governance approvals stop being trapped in the channel that raised them
([ADR-0018](docs/adr/0018-resumable-approval-seam.md)). The first v0.5 phase that
changes runtime behaviour, and the prerequisite for approve-later over Telegram
(phase 04) and standing jobs (phase 06).

- A new additive `pending_approvals` table is the single source of truth for a
  held turn (message, persona, auto_mode, summary, why, status). `Response.approval`
  becomes a typed `ApprovalRequest`, and one `master.resume_approval(decision_id,
  choice)` resolves the record + re-issues the turn from it (idempotent).
- A turn gated in one channel can now be approved in another: REPL `/pending` +
  `/pending approve|decline <id>`, web Approve/Deny (holding only the decision_id),
  and `ubongo pending` / `ubongo approve|decline <id>` from the CLI. MCP surfaces
  the `decision_id` so a human channel can resolve it (still never approvable over
  MCP — ADR-0015 holds).
- Behaviour-neutral for auto / reject / ask_clarification; additive schema, no
  migration; single-writer rule untouched. 980 tests green (+20).

## v0.5.2 — store split (v0.5 trust-protocol, phase 02)

Date: 2026-06-13

Opens the **v0.5.x** release line for the trust-protocol plan
([Plans/v0.5-trust-protocol.md](Plans/v0.5-trust-protocol.md)); the version follows
the phase branch (`v0.5/02-store-split` → `0.5.2`). Phases 00 (ledger) and 01
(envelope) merged before the versioning was settled, so their would-be `0.5.0` /
`0.5.1` tags were never cut; this entry records all three groundwork phases for
completeness. From here the bump is computed in CI from the merged branch name.

- **Phase 00 — Reconcile the ledger.** Archived the completed-but-unclosed
  `complete-fanout-peer-replacement` openspec change, synced its spec, and restated
  the runner's provisional fan-out-recovery wording as the accepted asymmetry
  (single-hop peer replacement in all five fan-out modes; the full Repair ladder
  stays sequential-only). Zero behavior change.
- **Phase 01 — The outer envelope** ([ADR-0017](docs/adr/0017-deployment-envelope-podman-nftables.md)).
  Deployment infrastructure under `deploy/envelope/`, zero `src/` LOC: a dedicated
  `ubongo` user, rootless Podman quadlets with `.env` mounted read-only, and a
  UID-keyed nftables egress allowlist (default `openrouter.ai`) so what leaves the
  machine is enumerable and enforced below the model's discretion. Linux/Pi only;
  the macOS dev machine is not enveloped.
- **Phase 02 — Split the store.** Behavior-free refactor of `memory/store.py`
  (1,990 lines / 92 functions) along its subsystem seams: `store.py` keeps the
  per-turn core; `trace.py` absorbs the four trace tables and their builders;
  `evolution_state.py`, `authoring_state.py`, and `index_state.py` own their
  subsystems' rows; `evaluation.py` holds the judge plumbing both sandboxes shared.
  Net `src/` LOC at baseline, single-writer rule untouched, 960 tests green.

## v0.1.5 — MCP client: the Connector agent

Date: 2026-06-12

The outbound half ([ADR-0016](docs/adr/0016-connector-agent-external-tools-one-seam.md)):
Ubongo can now call external MCP servers — Compendium first — through one
governed seam, shipped as candidate 20.

- The **Connector agent** (ninth worker, `composer=False`) discovers the tools
  on the servers declared in `settings.yaml::mcp.servers`, plans calls with
  its model, executes them via the new `mcp/client.py` session layer (stdio +
  streamable HTTP, per-turn sessions, lazy SDK import), and returns results as
  Findings for the persona to compose from. The first-class tool layer was
  considered and stays unjustified; the CLI-bridge option was rejected to keep
  the sandbox's no-network guarantee intact.
- **Opt-in**: `connector_session` is declared but not auto-routed — reach it
  with `/mode connector_session` (the `execution_session` precedent).
- **Governance**: connector workflows are irreversible; turn risk escalates to
  the highest enabled server's declared `risk:` (low-risk read-only servers
  stay auto; high-risk ones hit the existing approval row). `[mcp]` joins the
  unified audit log.
- **Degrades, never breaks**: no SDK / no servers / no tools produce honest
  findings; failed calls enter the Repair ladder (peer: architect), so a dead
  server still yields a normal answer.
- Config carries no secrets (`env:` maps resolve from `.env` at connect time).
  17 new tests; smoke gains deterministic checks plus a live **loop-back**
  (Ubongo's own MCP server as the client's peer); playbook section C.1–C.7.

## v0.1.4 — MCP server channel

Date: 2026-06-11

Ubongo becomes reachable by other agents ([ADR-0015](docs/adr/0015-mcp-server-additive-channel.md)):
an MCP server as the fourth additive channel, shipped as candidate 13.

- Tools: `ubongo_send` runs one full governed turn through `master.handle`
  (exactly like one-shot — same pipeline, same governance, same memory write);
  a turn the gate holds returns `gated=true` and **cannot be approved over
  MCP**. `ubongo_recall` is read-only recall (summary + recency + semantic).
- Resources (read-only): `ubongo://vault/daily/today` and `ubongo://audit`.
- Transports: stdio (`python -m ubongo mcp`, for clients that spawn the
  server) and streamable HTTP (`./start-ubongo-mcp.sh` or `ubongo mcp --http`,
  port 8765) with the same home-LAN, no-auth posture as the web UI.
- The official `mcp` SDK is an optional extra (`./install.sh --mcp` /
  `uv sync --extra mcp`); the core never imports it. `ubongo-ctl.sh`
  generalizes to `start|stop|restart|status [web|mcp]` (default `web`,
  backward compatible) and `deploy/ubongo-mcp.service` mirrors the web unit.
- 14 new tests (929 total); smoke gate gains an in-memory MCP handshake and
  an HTTP service cycle; playbook section M.1–M.8.
- The client half (Ubongo consuming external MCP servers like Compendium) is
  the next layer (v0.1.5), not this one.

## v0.1.3 — Local profiler + service control

Date: 2026-06-11

Local-only observability ([ADR-0014](docs/adr/0014-local-only-observability-profiler.md))
and operational control for the web service. Built as three candidates (10–12),
shipped as this version.

- `/profile [agents|models|modes] [N]` aggregates the latency/token/outcome data
  every turn already persists (`workflow_runs` / `agent_runs`) into on-demand,
  read-only summaries and breakdowns — no new tables, no event handlers, the
  single-writer rule untouched.
- `/profile cpu on|off|status` (and `ubongo send --profile`) wraps the turn's
  `master.handle` in stdlib `cProfile`: a `.prof` artifact under `data/profiles/`
  plus a top-25 cumulative summary. `/profile mem on|off|status` + `/profile mem`
  is the tracemalloc half: baseline on arm, allocation-growth report on demand,
  for leak hunting across a long-lived session. Profiling is opt-in, zero
  overhead when off, and can never break a turn.
- A startup switch: `--profile [cpu|mem|all|off]` on launch or `UBONGO_PROFILE`
  in `.env` (flag wins) arms the same toggles from boot; the web turn path arms
  CPU the same way.
- `ubongo-ctl.sh start|stop|restart|status` manages the web UI as a background
  service (pidfile + log under `data/`); `deploy/ubongo-web.service` is the
  systemd alternative for the Pi. Both ship in the deployment bundle.
- 41 new tests (`tests/test_profiling.py`); smoke playbook section P.1–P.16; the
  full cumulative smoke re-certified end-to-end with the profiler armed.
- Distribution moves onto GitHub Actions: CI runs the suite, the automated
  smoke gate (`scripts/smoke.sh` — the scriptable subset of the manual
  playbook: cold start, command surfaces, sandbox refusals, the profiler
  family, the startup switch, web service control), and a bundle build check
  on every PR. Merging a `VERSION` bump to `main` re-runs tests + smoke
  (plus a small live-model subset when an `OPENROUTER_API_KEY` secret is
  configured), then tags `v<VERSION>` and publishes a GitHub Release carrying
  the two distribution files (`install-ubongo.sh` + the bundle zip), with that
  version's changelog section as the release notes. `package.sh` now fails
  hard on a VERSION/pyproject mismatch and marks `ubongo-ctl.sh` executable.

## v0.1.2 — Self-authored skills

Date: 2026-06-10

The self-extension experiment: Ubongo drafts brand-new skills behind a human
approval boundary ([ADR-0013](docs/adr/0013-self-authored-skills-quarantine-and-approval.md)).
Built as five internal phases, shipped as this version.

- `/author <description>` drafts a skill, validates it (schema reuse plus a
  command-skill risk floor enforced in code), and quarantines it where the runtime
  cannot see it.
- `/skill-candidates approve | reject | rollback` is the approval gate, with
  versioned backups (a re-author backs up the prior version; rollback restores it).
- An autonomous authoring daemon (`/authoring status|pause|resume|off`) infers
  recurring capability gaps and drafts candidates; it boots paused, is throttled,
  and only ever drafts. Approval always stays manual.
- Docs: ADR-0013, the SECURITY threat model, a turn flow + UML sequence diagram,
  agent diagrams, the project logo, a rewritten README, and this version-tracking
  setup (`VERSION` + this changelog).

## v0.1.1 — Web UI

Date: 2026-06-07

An optional self-hosted Streamlit chat page (`./start-ubongo-web.sh`): an additive
channel that reuses the same `master.handle` turn seam as the CLI, so governance and
the sandbox are unchanged. Off unless installed with `./install.sh --web`; LAN-only
by design (no auth, no TLS).

## v0.1.0 — v0.1 build complete

Date: 2026-06-04

The original 22-phase build plan, all six tiers: Foundation; the Multi-Agent system
(Master Agent + ten worker agents + six execution modes); Self-Healing (the repair
ladder); Governance (the decision matrix + interactive approval gate + hardened
sandbox); the Self-Improvement genetic-programming loop (human-approved promotions
over prompts and routing/tool-chain/retry config); and Wiki Memory + Polish
(sqlite-vec semantic recall, the vault-link graph, bidirectional vault sync, unified
audit). Also includes the post-v0.1 behavior-neutral architecture-deepening refactors
([ADR-0012](docs/adr/0012-agent-envelope-directives-and-router-planning.md)).
