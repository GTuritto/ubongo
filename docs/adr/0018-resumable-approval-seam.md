# 0018 — The approval exchange is a typed, persisted, resumable seam

Status: Accepted
Date: 2026-06-13

## Context

When the governance matrix returns `require_approval`, the turn must wait for a
human yes/no. Through Phase 15 that exchange lived only in memory: `master`
attached `Response.approval` as a dict `{decision_id, summary, why}`, and each
channel hand-rolled the resume — the REPL re-issued `handle(approved=True)` from
its loop locals, the web channel stashed the whole turn in `st.session_state`,
one-shot exited 1 with no way back, and MCP returned `gated=True` as a dead end.
Resume only worked while the *requesting channel still held the turn in memory*;
a process restart or a different channel lost it.

v0.5 Phase 04 (Telegram) and Phase 06 (standing jobs) both need approve-*later*
across a process/channel boundary. The in-memory exchange cannot provide it.

## Decision

The pending-approval **record** is the single source of truth for resume.

- A new additive table `pending_approvals` (keyed by `decision_id` → 
  `governance_decisions.id`) persists everything needed to re-issue a gated turn:
  `message`, `persona`, `auto_mode`, `summary`, `why`, `status`
  (`pending|approved|declined`), `created_at`, `resolved_at`. CRUD lives in
  `memory/trace.py`, next to `governance_decisions` — they are keyed together and
  resolved together, and both follow the existing governance carve-out (written
  by the orchestrator, not the Memory Agent; ADR-0002 is about *durable user
  memory*, which this is not).
- `Response.approval` is the typed `ApprovalRequest` (frozen dataclass), not a
  dict. `governance/approval.py` owns the types and read wrappers and imports no
  `master` (avoiding the cycle).
- **One resume function**, `master.resume_approval(decision_id, choice)`, is the
  only path every channel uses: it resolves the record + writes
  `governance_decisions.approval_response`, and on approve re-issues
  `handle(message, persona, auto_mode, approved=True)` *from the record* and
  flushes delivery. It is idempotent — an unknown or already-resolved id returns
  `None` and re-runs nothing.
- Channels are thin adapters: REPL prompts then calls `resume_approval` (+ a
  `/pending` listable surface); web holds only the `decision_id`; one-shot gains
  `ubongo pending` / `ubongo approve|decline <id>`; MCP surfaces the
  `decision_id` so a human channel can resolve it (still never approvable over
  MCP itself — ADR-0015 holds).

## Consequences

- **The invariant:** no channel re-implements the resume contract or stores the
  gated turn in session state. A turn gated in one channel can be approved in any
  other, or after a restart — the headlessly-testable exit criterion this phase
  was built around.
- The gated turn's `message` now lives in a new table. Same store, same
  single-writer posture and local-only trust boundary as `messages`; no new
  external exposure.
- MCP gated turns stop being dead ends: the persisted record + a human channel
  give them an approve-later path without weakening the no-approval-over-MCP
  rule.
- This is the seam Phase 05's grant registry hangs its first-encounter asks on,
  and the mechanism Telegram needs to carry a y/n across the network.
