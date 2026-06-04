# 0011 — Vault sync via a polling watcher + conflict queue + unified audit

Status: Accepted
Date: 2026-06-04 (decision dates to Phase 21)

## Context

The vault was a one-way projection: the system wrote daily notes, but the user's
Obsidian edits were invisible to it. Closing the loop raises three questions.
(1) How to watch for edits: the spec named `watchdog`, but that adds a dependency
against the lean-deps rule. (2) How to resolve edit/write conflicts: a background
watcher cannot invoke the interactive Phase-15 approval prompt mid-turn. (3) The
system writes the vault every turn, so a naive watcher re-ingests its own writes
forever (an echo loop).

## Decision

- **Polling watcher, no dependency** (`memory/vault_watch.py`). A `VaultWatcher`
  daemon thread (mirroring `evolution.loop.EvolutionLoop`) scans the vault every
  N seconds by mtime+hash. No `watchdog`; honors the lean-deps rule; `scan_once()`
  is a pure, testable entry point. Off by default (`vault.sync.enabled`).
- **Echo suppression via `vault_state`.** The system records the content hash of
  every note it writes. The watcher compares disk hash to it: match → its own
  write (skip); differ → an external edit → ingest (re-embed into `vec_vault`).
  No echo loop.
- **Conflict queue + `/conflicts`, not a mid-turn prompt.** A collision on a
  system-managed note is recorded in `vault_conflicts` (never clobbered) and
  resolved explicitly (`keep-mine` / `keep-theirs` / `merge`). Because daily
  notes are append-only, the practical resolution is usually "coexist" — the
  keep-mine/merge paths exist for correctness, not heavy use (stated plainly).
- **Unified audit** (`vault/system/audit.md`). Governance, evolution, and sync
  decisions share one human-readable, Obsidian-friendly file with a `[category]`
  tag; the file is the source of truth, `/audit` tails it. Phase 19's
  evolution-only audit redirects here via a back-compat shim.

## Consequences

- Bidirectional vault sync with zero new dependencies and no OS event backend.
- The watcher is a second background daemon alongside the GP loop; both are
  off-by-default, started/stopped by the REPL, and kept off in tests.
- Conflict handling is honest about append-only notes rather than overstated.

References: `Plans/phase-21-vault-sync-audit.md`; `src/ubongo/memory/{vault_watch,vault,store,embeddings}.py`, `src/ubongo/{master,repl}.py`.
