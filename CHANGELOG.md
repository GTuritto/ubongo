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
