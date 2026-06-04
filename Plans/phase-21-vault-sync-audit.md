# Phase 21 — Bidirectional Vault Sync + Audit + End-to-End Tightening: Implementation Plan

Date: 2026-06-04
Branch: `phase-21-vault-sync-audit` (off `main` at `123d126`)
Spec source: [UBONGO_BUILD.md](../UBONGO_BUILD.md) §Phase 21.
Tier: 6 — Wiki Memory + Polish (final phase). **This completes v0.1.**

## Context

The vault has been a one-way projection: the system writes daily notes, but
edits the user makes in Obsidian are invisible to it. Phase 21 closes the loop —
a background watcher ingests external edits (re-embedding them so semantic recall
sees them), edit/write collisions are queued for the user to resolve, governance
and evolution decisions land in one unified audit log, `/reload` hot-reloads
settings, and a final end-to-end smoke pass certifies v0.1.

Verified during planning:
- **`watchdog` is not installed**; per your decision the watcher is a **polling
  daemon thread** (no new dependency, mirroring `EvolutionLoop`).
- **`vec_vault`** was created in Phase 20 but never populated — Phase 21 fills it
  from vault notes.
- **`config._cache`** is shared (per-path) across `settings.yaml` and
  `governance.yaml`, so `config.reload()` re-reads both — settings hot-reload is
  a one-line addition to `_reload_all`.
- Daily notes are **append-only** by the system, so genuine destructive write
  conflicts are rare (flagged honestly below).

## Decisions locked with the user

- **Watcher = polling thread, no new dep.** A `VaultWatcher` daemon scans the
  vault every N seconds by mtime+hash and ingests changed files. It distinguishes
  the system's own writes from user edits via a recorded content hash (no echo
  loop). Satisfies the spec's "~5s" latency; off by default, like the GP loop.
- **Conflicts = queue + `/conflicts` command.** A background thread can't prompt
  mid-turn, so a detected collision is recorded (never clobbered) and resolved
  explicitly via `/conflicts` (keep-mine / keep-theirs / merge) in the same prompt
  style as approvals.

## Design

### Tracking system writes vs. user edits (the echo problem)

The system appends to daily notes every turn; a naive watcher would re-ingest its
own writes forever. New **`vault_state(path PRIMARY KEY, content_hash,
last_written_at)`** records the hash of what the system last wrote. The poller
compares each file's current on-disk hash to its `vault_state` hash: equal → the
system's own write (skip); different → an **external edit** (ingest). The Memory
Agent updates `vault_state` whenever it writes a note (`append_to_daily_note`,
`append_audit`).

### 21a — VaultWatcher (polling daemon)

`memory/vault_watch.py` (new), modeled on `EvolutionLoop`: a daemon thread with
injectable `sleep`/tick, `start()` / `stop()`, guarded by `vault.sync.enabled`
(default false; a `UBONGO_DISABLE_VAULT_WATCH` off-switch keeps the suite quiet).
Each tick scans `vault/daily/*.md` (and `vault/system/*.md` read-only) by mtime;
for files whose hash diverges from `vault_state`, it triggers ingest. The REPL
starts it after session setup and stops it on every exit path (the
`run()`/`_repl_loop()` try/finally already added in Phase 18).

### 21b — Ingest pipeline + conflict queue

- **Ingest (re-embed):** an external edit triggers the Memory Agent to (re)embed
  the note's content into **`vec_vault`** (idempotent via the `vault_state`
  hash). This is the first population of `vec_vault`; a follow-up extends
  semantic recall to search it, but v0.1 scopes recall to messages and uses
  `vec_vault` for vault-note retrieval surfaced in `/recall`. Best-effort: a
  disabled/unavailable embeddings layer skips ingest silently.
- **Conflict detection:** new **`vault_conflicts(id, path, detected_at,
  system_hash, disk_hash, status ['open'|'resolved'], resolution)`**. When the
  poller finds an external edit to a file the system also has pending/just wrote
  (hash mismatch within a write window), it records an `open` conflict instead of
  clobbering. Because daily notes are append-only, the common case resolves to
  "coexist" (both kept) — documented; the queue + command exist for the general
  case and the spec scenario.
- **Resolution:** `/conflicts` lists open conflicts; `/conflicts resolve <id>
  <keep-mine|keep-theirs|merge>` applies it (keep-theirs ingests the edit and
  updates `vault_state`; keep-mine restores the system's last-written content
  from a snapshot; merge keeps both). Uses the approval prompt phrasing.

### 21c — Unified audit log

`vault/system/audit.md` (new), via a generalized **`vault.append_audit_entry(
category, line)`** (`category ∈ governance | evolution | sync`). Routes:
- **governance** — gated decisions (`require_approval` / `reject` /
  `ask_clarification`) and approvals, written from `master.handle`.
- **evolution** — `promotion.py`'s approve/reject/rollback (migrated from the
  Phase-19 `evolution-audit.md` to the unified writer; the old file is kept as a
  back-compat alias write or redirected — TBD, leaning redirect with a one-time
  note).
- **sync** — ingests and conflict resolutions.

### 21d — `/audit [category] [N]`

Tail the unified audit (default last 20), optionally filtered by category. A
direct read tool. Reads `audit.md` (or a small `audit_entries` table — see
"Open question" — leaning on the markdown file as the source of truth with a tail
parse, to keep the audit human-readable in Obsidian).

### 21e — Settings hot-reload

Extend `_reload_all()` to also `config.reload()` (re-reads settings.yaml +
governance.yaml — shared cache) and `router.reload()`, in the right order
(`config.reload()` before `personas.reload()` so a changed `models.casual` is
picked up on the next casual turn). Message updates to "Reloaded settings,
UBONGO.md, personas, skills, and routing."

### 21f — Final smoke pass

Append a Phase 21 section to `tests/manual/smoke_test.md` (edit-a-note loop,
`/conflicts`, `/audit`, hot-reload) and run the **entire** cumulative playbook
(Phases 0–21) end to end — the v0.1 certification.

## Files touched

New:
- `src/ubongo/memory/vault_watch.py` — `VaultWatcher` poller + ingest trigger.

Modified:
- `src/ubongo/memory/vault.py` — content hashing, `vault_state` update on write,
  generalized `append_audit_entry` + `audit.md`, scan helper.
- `src/ubongo/memory/embeddings.py` — `index_vault(path, text)` into `vec_vault`.
- `src/ubongo/agents/memory.py` — ingest-on-edit (re-embed) hook.
- `src/ubongo/memory/store.py` — `vault_state` + `vault_conflicts` accessors;
  unified audit readers.
- `src/ubongo/governance/decision.py` / `master.py` — write governance decisions
  to the unified audit.
- `src/ubongo/evolution/promotion.py` — route audit to the unified writer.
- `src/ubongo/repl.py` — `/audit`, `/conflicts`, `_reload_all` extension, watcher
  start/stop, help.
- `src/ubongo/memory/schema.sql` — `vault_state`, `vault_conflicts` tables.
- `config/settings.yaml` — `vault.sync.enabled` + `poll_interval_s`.

Additive tables only; vec_vault already exists — **no destructive migration**.

## Tests

Unit (`tests/`), embeddings + watcher off by default; ingest tests mock `embed`:
- `test_vault_watch.py` — `VaultWatcher` scan detects an external-edit (hash ≠
  `vault_state`) and skips the system's own write (hash ==); start/stop;
  `_should_scan` gate; off when disabled.
- `test_vault_ingest.py` — an external edit re-embeds into `vec_vault`
  idempotently; `vault_state` updates on system write; ingest is a no-op when
  embeddings off.
- `test_vault_conflicts.py` — a collision records an `open` conflict (no
  clobber); `/conflicts resolve` keep-mine / keep-theirs / merge transitions;
  store accessors.
- `test_audit_unified.py` — governance + evolution + sync entries land in
  `audit.md` under their category; `append_audit_entry`; promotion routes here.
- `test_repl_audit_conflicts.py` — `/audit [category] [N]` filtered tail;
  `/conflicts` list + resolve render; both in help.
- `test_repl_reload.py` (extend) — `_reload_all` clears the config cache so a
  changed model is read next turn; new message text.

Spec scenario coverage:

| # | Scenario | Covered by |
| --- | --- | --- |
| 1 | Vault edit ingestion (~5s) | `test_vault_watch` + `test_vault_ingest` (+ live smoke) |
| 2 | Conflict prompt (keep/yours/merge) | `test_vault_conflicts` + `test_repl_audit_conflicts` |
| 3 | Audit log `/audit` | `test_audit_unified` + `test_repl_audit_conflicts` |
| 4 | Settings hot-reload | `test_repl_reload` |
| 5 | Full smoke | the Phase 21 final smoke pass (manual) |

## Smoke additions

A Phase 21 section: enable `vault.sync.enabled`; edit a daily note in a text
editor and confirm `/recall` / logs show the ingest within the poll interval;
trigger and resolve a conflict via `/conflicts`; `/audit governance` and
`/audit evolution` tails; edit `models.casual`, `/reload`, confirm the next
casual turn uses the new model. Then the **full cumulative playbook (0–21)**.

## Risks / honest flags

- **Append-only conflicts are mostly trivial.** The conflict queue + command are
  built for the general case and the spec scenario, but with append-only daily
  notes the honest resolution is usually "coexist." I will not pretend the
  keep-mine/merge paths are heavily exercised in normal use; they are there for
  correctness and the non-append future.
- **Poller latency + cost.** Re-embedding on every external edit is one embed
  call per changed file; best-effort and skippable via the flag. The poll
  interval trades latency for idle work; default ~5s.
- **The watcher is another background thread** alongside the GP loop. Both are
  daemon threads started/stopped by the REPL; tests keep them off.

## Open question (resolve during build, not blocking)

Audit source of truth: the human-readable `audit.md` file (tail-parsed by
`/audit`) vs. an `audit_entries` table (queried, with the file as a projection).
Leaning **file-as-truth** to keep it Obsidian-readable and avoid another table;
will revisit if filtering needs structure.

## Branch workflow

1. `git switch -c phase-21-vault-sync-audit` off `main` at `123d126`.
2. First commit flips STATUS.md Phase 20 → Complete (merged via PR #15,
   `4c3193b`) — the deferred doc fix — alongside this plan reference. Push; open a
   **draft** PR.
3. Implement: 21e hot-reload (small, lands first) → 21c unified audit → 21a
   watcher + `vault_state` → 21b ingest + conflict queue → 21d `/audit` +
   `/conflicts`. Tests alongside.
4. Full pytest (currently 701; Phase 21 adds ~30) + the Phase 21 smoke section +
   **the full cumulative smoke pass** + a bounded live run (edit a note, see
   ingest).
5. Mark ready; user reviews, user merges. **v0.1 is done when this merges.**

## Acceptance

- All five spec scenarios pass; the full cumulative smoke test passes without
  manual fixup; full pytest green.
- External vault edits ingest (re-embed); collisions queue and resolve via
  `/conflicts`; governance + evolution land in `vault/system/audit.md` with
  `/audit`; `/reload` hot-reloads settings; the watcher is a no-dep polling
  daemon, off by default, started/stopped by the REPL.
- Additive tables only; no destructive migration. LOC under the ~15k soft target.
- The v0.1 acceptance criteria in `STATUS.md` are all met.
