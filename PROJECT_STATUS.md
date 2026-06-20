# Ubongo — Status Briefing

*2026-06-20, from the code on branch `v0.5/04-telegram`. 1,010 tests collected (96 test modules). This is a fast catch-up, not an architecture doc.*

## Where we are against the plan

v0.1 (the 22-phase CLI build) is certified complete and on `main`, along with a post-v0.1 layer (web UI, self-authored skills, profiler, MCP server, MCP client/Connector). The active work is the **v0.5 trust-protocol plan** ([Plans/v0.5-trust-protocol.md](Plans/v0.5-trust-protocol.md)), an eight-phase arc (00–07) that hardens the system for a cloud-relayed messaging channel and standing autonomous jobs. The phases are not built in strict numeric order.

Done and merged to `main`: **Phase 00** (reconcile the ledger), **Phase 01** (the outer envelope — rootless Podman quadlets + nftables egress allowlist), **Phase 02** (split the store into five table-family modules + shared judgment parsing), **Phase 03** (the typed resumable approval seam), and **Phase 05** (the grant registry + the Connector armed; PR #51). Released versions ran up to v0.5.5.

In progress: **Phase 04 — Telegram** (the channel proper). It is committed on the current branch (`feat: the Telegram channel`, plus its plan commit) but **not yet merged to `main`** — no merge commit sits above it in the log, so the phase PR is still open per the branch-per-phase workflow. *Inferred from log shape; confirm with `gh pr status`.*

Not started: **Phase 06** (standing jobs — the v0.3 proactive-output seam) and **Phase 07** (the contract and identity — backup/portability/the trust contract).

## What's implemented and working

The v0.5 additions all carry tests, exercised not merely present:

- **Approval seam (Phase 03).** `test_approval_seam.py`, `test_governance_approval.py`, `test_repl_approval.py` cover the typed `ApprovalRequest`, the persisted `pending_approvals` record, and `master.resume_approval` — a turn gated in one channel resolved from another. This is the real cross-channel approve-later path, not a stub.
- **Grant registry (Phase 05).** `test_grant_registry.py` exercises the `grants` table, the post-safety governance rule, grant-on-approval, revocation surviving restart, and the `/grants` + `ubongo grants` surfaces. The paired cut — deleting the `retry:repair` evolvable target — is done.
- **Telegram channel (Phase 04).** `test_telegram_service.py` (the network-free core: `allowed_user_ids` auth, the `/approve|/decline|/pending|/grants` router, turn handling over `channel.run_turn`) and `test_telegram_bot.py` (the httpx long-poll Bot-API layer). `httpx` is an optional extra, lazily imported.
- **Store split (Phase 02).** `store.py` dropped from ~1,990 to 592 lines; the sibling modules (`trace.py`, `evolution_state.py`, `authoring_state.py`, `index_state.py`) each have their own suites. Behavior-neutral; single-writer rule intact.
- **The outer envelope (Phase 01).** `deploy/envelope/` ships the Containerfile, quadlets, nftables config, and the egress-refresh timer plus `INSTALL.md`, enforced on the Linux/Pi box — **not** covered by pytest (it lives outside `src/`), so its correctness rests on manual/deployment verification, not the suite.

Everything from v0.1 and the post-v0.1 layer remains exercised: the Master pipeline, all six execution modes, the governance matrix, the GP and authoring loops, semantic recall, vault sync, and the four prior channels.

## What's missing or unfinished

**(a) Planned but not built.** Phase 06 (standing/proactive jobs) and Phase 07 (the trust contract + backup/identity/portability) are not started. The notification-policy engine (quiet hours, holds, catch-up summarizer) is still deferred.

**(b) Partial or stubbed.** Telegram (Phase 04) is functionally complete with tests but unmerged — treat it as "ready for review," not shipped on `main`. Approval has no expiry/escalation: a `pending_approvals` row can sit unanswered indefinitely, which becomes a real gap once proactive jobs (Phase 06) can gate a turn with no human in the loop. Per-tool grant granularity is deferred — grants are server-granular only.

**(c) Known gaps and constraints.** The egress envelope is enforcement outside the test suite, so a change to what the Connector or a CLI script can reach passes pytest yet may be silently blocked/allowed on the deployed box. The trust posture stays single-user: Telegram auth is a flat `allowed_user_ids` allowlist (empty = deny all), no per-request auth or TLS in the app. LOC is ~16,060, ~7% over the ~15,000 soft target — the project's own rule says cut, not expand, which puts Phase 06/07 scope under pressure.

*Verified against the test inventory, git log, and the v0.5 plan on 2026-06-20. The one inference (Phase 04 PR still open) is flagged above.*
