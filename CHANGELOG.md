# Changelog

Ubongo versioning is **v0.MAJOR.PHASE** (pre-1.0):

- **MAJOR** bumps when a whole *build plan* completes. `v0.1` is the original
  22-phase build (the multi-agent, self-improving CLI).
- While the next build plan is in progress, **PHASE** (the third number)
  increments by 1 for each completed phase. When that build plan ships, MAJOR
  bumps and PHASE resets to 0.

So while building toward **v0.2 (Telegram)**, the version walks `v0.1.1`,
`v0.1.2`, …; when v0.2 ships it becomes `v0.2.0`, and the phases of the v0.3 build
then walk `v0.2.1`, `v0.2.2`, ….

The current version is the single line in [`VERSION`](VERSION) (kept in sync with
`pyproject.toml`; the packaging script reads `VERSION` for the bundle name). Each
entry below records what that version added. Newest first.

---

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
- Distribution moves onto GitHub Actions: CI runs the suite + a bundle build
  check on every PR, and merging a `VERSION` bump to `main` automatically
  tags `v<VERSION>` and publishes a GitHub Release carrying the two
  distribution files (`install-ubongo.sh` + the bundle zip), with that
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
