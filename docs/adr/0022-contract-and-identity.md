# 0022 — The contract and identity: verbosity as config, an instance is its data

Status: Accepted
Date: 2026-06-20

## Context

Phase 07 is the final phase of the v0.5 trust protocol, and deliberately the
lightest. Two questions were left for it. First, **how much** should Ubongo say —
a turn's length had been whatever the persona produced, with no lever. Second,
**what is an Ubongo instance**, concretely, when you want to back it up or move
it. Both touch trust: a length knob that *learned* its own boundaries would be a
self-modification risk, and a backup that carried secrets or standing consent
would move trust across a boundary silently.

## Decision

**Verbosity is governance config, manual-first.** A `verbosity:` block in
`governance.yaml` maps a domain (the classifier's `task_type`, then `intent`) to
a level (`terse | normal | deep`), with a default. `governance/verbosity.py`
resolves it; `master.plan` sets the level on the `Workflow`; the runner passes it
as a typed `AgentDirectives.verbosity`; the composer persona appends **one**
length line under its body. `normal` is a no-op — the knob only ever shortens or
lengthens on an explicit mapping, never rewrites the voice. `/verbosity` shows
the live table (read-only); `/brief` and `/verbose` override one turn (the
one-shot directive seam, like `/skill`).

Learned legibility — a GP-evolvable `verbosity:<domain>` target — is **deferred**.
The GP loop is the natural machinery, but it is out of scope here, and crucially
it is *safe whenever it lands*: promotion stays human-approved (ADR-0006), so the
boundary cannot move itself. The dangerous half of "learning the boundaries" is
structurally impossible in this codebase.

**An instance is its data + config.** `ubongo backup` writes a portable `tar.gz`
of exactly `data/ubongo.db` + `vault/` + `config/` (settings, governance, jobs,
personas, skills). It **never** includes `.env` (secrets stay out) and never the
disposable `data/profiles/`. There is no install log to replay — capabilities are
the human-approved config allowlist, which *is* config. Restore is unpacking the
archive into a fresh checkout. **Grants do not migrate**: restore re-arms them by
default (revokes active grants in the restored DB), so the first connector turn on
the new envelope asks again — a moved instance crosses a new trust boundary.
`--keep-grants` preserves them for same-machine disaster recovery.

**Forking, naming-as-a-speech-act, and inter-instance skill exchange are
designed-but-deferred** — recorded here as deliberate non-goals, not omissions.
They wait until a second instance has a reason to exist; when it does, authored
skills already carry quarantine-and-approve (ADR-0013) as their import path.

## Consequences

- Verbosity adds no seam: it is one more legible config consulted at
  prompt-assembly time, the same shape as the live-swap reads, degrading to
  `normal` when the block is absent. The human floor (ADR-0006) stays intact, so
  the later learned variant is safe by construction.
- A backup is a faithful, secret-free copy an operator can move or archive. The
  trust boundary is explicit: secrets never travel (re-enter `.env` by hand),
  consent never travels (grants re-arm), and identity is just the files.
- The deferred items are named, so a future "second instance" feature starts from
  a decision, not a blank.
- **The v0.5 trust protocol is complete.** Phases 00–07 are merged; the protocol's
  arc — reconcile, envelope, store split, approval seam, Telegram, grants,
  standing jobs, contract — is closed.
