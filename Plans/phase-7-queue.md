# Phase 7 — Minimal Outbound Queue: Implementation Plan

Date: 2026-05-12
Branch: `phase-7-queue` (off `main` at `89e2228`)
Spec source: [UBONGO_BUILD.md](../UBONGO_BUILD.md) §Phase 7 (lines 872–898), `notification_queue` schema (438–448), invariant 9 (line 34), pipeline diagram (84–90).

## Goal

Every LLM-generated response that lands on stdout first writes a row to the `notification_queue` SQLite table, fires `before_send`, prints, fires `after_send`, then marks the row `delivered_at`. The queue is the seam that lets Phase 8+ swap CLI for Telegram (v0.2) or proactive jobs (v0.3) without restructuring. A new `/queue` REPL command exposes the last N rows for inspection.

## Why this plan exists

Three patterns Phase 7 locks in that downstream phases inherit:

1. **`notification_queue` becomes the delivery seam.** Once Phase 8 lands the Master Agent, it'll call `enqueue` at the end of `handle()` rather than print directly. v0.2 Telegram is "add a new transport that consumes `dequeue_deliverable`". Get the queue API shape right now.
2. **`before_send` event ships.** Today only `after_send` exists. Phase 8 needs `before_send` for the Master Agent's "policy gate before flush"; Phase 14 governance hangs off it too. Adding the event with one passthrough dispatch site now means later phases can attach without refactoring.
3. **`after_send` becomes a delivery-side-effect, not an LLM-side-effect.** Today vault writes on LLM success. Phase 7 makes it write on print success. Same observable behavior for the happy path; cleaner semantics for v0.2 Telegram (vault writes once the message actually leaves).

The `notification_queue` table is already in [schema.sql](../src/ubongo/memory/schema.sql#L116) so no migration is needed; we only have to start using it.

## Branch + commit strategy

Branch: `phase-7-queue` off `main` at `89e2228`. Four commits:

- **7a** — `delivery/queue.py` API + `test_delivery_queue.py`.
- **7b** — Refactor `handle_text` send path through enqueue/dequeue; add `before_send` event; move `after_send` to post-print. Update tests.
- **7c** — `/queue` REPL command + tests.
- **7d** — STATUS + smoke playbook Phase 7 section.

## Sub-phases

### 7a — Queue API (`src/ubongo/delivery/queue.py`)

**Purpose:** A small, synchronous, SQLite-backed queue. No worker thread, no polling, no async.

**Tasks:**

1. Create `src/ubongo/delivery/__init__.py` and `src/ubongo/delivery/queue.py`.
2. Define `@dataclass(frozen=True) class QueueRow`:
   - `id: int`, `content: str`, `urgency: Literal["low","normal","urgent"]`, `source: str | None`, `created_at: str`, `deliver_after: str | None`, `delivered_at: str | None`, `expires_at: str | None`, `metadata: dict[str, Any] | None`.
3. Functions:
   - `enqueue(content, urgency="urgent", source=None, deliver_after=None, expires_at=None, metadata=None) -> int` — INSERT; returns `id`. `metadata` is JSON-serialized.
   - `dequeue_deliverable(now=None) -> QueueRow | None` — returns the oldest row with `delivered_at IS NULL` AND (`deliver_after IS NULL` OR `deliver_after <= now`) AND (`expires_at IS NULL` OR `expires_at > now`), ordered by `urgency DESC` (urgent > normal > low) then `created_at ASC`. Returns `None` if nothing deliverable. Does NOT mark delivered — caller decides.
   - `mark_delivered(row_id, when=None) -> None` — UPDATE `delivered_at = when` (or `now_iso()`).
   - `last_n(n=10) -> list[QueueRow]` — ordered by `created_at DESC`, mapped to `QueueRow`.
4. Uses `store.connection()` — no new bootstrap path; the schema already includes `notification_queue` ([schema.sql](../src/ubongo/memory/schema.sql#L116)). All timestamps via `store.now_iso()` so `UBONGO_FAKE_NOW` keeps working.
5. Tests in `tests/test_delivery_queue.py` (~8 tests):
   - enqueue + dequeue round-trip (single row).
   - urgency ordering (`urgent` dequeued before `normal` before `low`).
   - `deliver_after` future timestamp is skipped.
   - `expires_at` past timestamp is skipped.
   - `mark_delivered` flips the row; next dequeue returns the following row (or `None`).
   - `last_n` returns rows in DESC `created_at` order.
   - `metadata` round-trips as JSON (`{"persona":"casual"}` → dict).
   - Empty queue → `dequeue_deliverable` returns `None`.

**Files added:** `src/ubongo/delivery/__init__.py`, `src/ubongo/delivery/queue.py`, `tests/test_delivery_queue.py`.

### 7b — Refactor response path through queue

**Purpose:** Wire `handle_text` to write the response through the queue before stdout.

**Tasks:**

1. In [src/ubongo/repl.py](../src/ubongo/repl.py) `handle_text()`, after the LLM call succeeds and the assistant message is appended:
   - Call `queue.enqueue(content=text, urgency="urgent", source="response", metadata={"persona": chosen, "auto_routed": auto_mode, "conversation_id": conv_id, "assistant_message_id": assistant_msg_id})`.
   - Call `queue.dequeue_deliverable()`; expect to get the same row back.
   - Dispatch `before_send` with `{"row_id": row.id, "content": row.content, "urgency": row.urgency, "source": row.source, "metadata": row.metadata}`.
   - Return `(text, ok, chosen, skill_name, delivery_token)` where `delivery_token` is an opaque tuple `(row_id, after_send_payload)` (or `None` on error / no-queue path). The print stays at the caller.
2. New helper `delivery.flush_delivered(token) -> None`:
   - Dispatch `after_send` with the existing payload.
   - Call `queue.mark_delivered(row_id)`.
   - Caller invokes after `print(text)`.
3. Caller wiring:
   - REPL loop ([repl.py:302–306](../src/ubongo/repl.py#L302-L306)): `text, _ok, used, _skill, token = handle_text(...)`; `print(text)`; `if token: delivery.flush_delivered(token)`.
   - `oneshot.run` ([oneshot.py:22–24](../src/ubongo/oneshot.py#L22-L24)): same pattern; return rc unchanged.
4. **Move** the `after_send` dispatch out of `handle_text` ([repl.py:135–148](../src/ubongo/repl.py#L135-L148)) into `flush_delivered`. Vault handler ([memory/vault.py:124](../src/ubongo/memory/vault.py#L124)) keeps working unchanged — it just fires slightly later in the flow.
5. Failure modes:
   - **Enqueue raises** → log `queue_enqueue_failed` (warning), return `delivery_token=None`. Caller still prints `text`. No `before_send` / `after_send`. Best-effort: we never lose user-facing output to a DB hiccup.
   - **Dequeue returns `None` right after enqueue** → log `queue_dequeue_inconsistent` (warning), return `delivery_token=None`. Same fallback.
   - **`mark_delivered` fails after print** → log warning inside `flush_delivered`. Row stays undelivered. No user-visible impact.
6. Error path (LLM fails, `ok=False`): also enqueue the polite-error string with `source="error"`, urgency `"urgent"`. Dispatch `before_send` but **not** `after_send` (vault should not log an LLM failure as a turn). Token still returned; `flush_delivered` skips `after_send` when payload has `ok=False` flag.
7. Tests:
   - `tests/test_delivery_path.py` (new):
     - Happy path: one send → exactly one row in `notification_queue` with `source='response'`, `delivered_at NOT NULL`; vault entry present.
     - Event order: registered `before_send` fires before stdout write; registered `after_send` fires after. Use a probe list with monotonic counters.
     - Error path: mocked LLM failure → row with `source='error'`, `delivered_at NOT NULL`; "Sorry…" on stdout; vault NOT written.
     - Enqueue failure: monkeypatch `queue.enqueue` to raise → text still printed; no before_send / after_send; warning logged.
   - Update `tests/test_repl_summary.py` and any test asserting `after_send` payload sequence (skim once 7b lands; vault test should remain green).

**Files modified:** `src/ubongo/repl.py`, `src/ubongo/oneshot.py`, `src/ubongo/delivery/queue.py` (add `flush_delivered`).
**Files added:** `tests/test_delivery_path.py`.
**Files unchanged but verified:** `src/ubongo/memory/vault.py`, `src/ubongo/events.py` (event names are by convention; no module change).

### 7c — `/queue` REPL command

**Purpose:** Operator-visible inspection of the last N rows.

**Tasks:**

1. In `repl.py`, add `_render_queue_table(n=10)`:
   - Call `queue.last_n(n)`.
   - Empty → `Queue is empty.`
   - Otherwise: header `Recent queue (last N):` then one line per row:
     `<id>  <HH:MM:SS>  <delivered HH:MM:SS or —>  <urgency>  <source or —>  <preview>`
     where `preview` is `content[:60]` with a trailing `…` when truncated.
2. Wire `/queue` into the slash dispatch. Parse optional integer arg: `/queue` → N=10; `/queue 25` → N=25; non-int → `Usage: /queue [N].`
3. Update the unknown-command help line in `handle_slash` ([repl.py:280–288 region](../src/ubongo/repl.py#L280)) to include `/queue`. Mirror update in [tests/manual/smoke_test.md scenario 1.7](../tests/manual/smoke_test.md#L35) expectation string.
4. Tests:
   - Empty queue → renders "Queue is empty."
   - After 3 turns: 3 rows rendered, all marked delivered, preview truncation works at >60 chars.
   - `/queue 1` returns the most recent row only.

**Files modified:** `src/ubongo/repl.py`, `tests/test_repl.py`, `tests/manual/smoke_test.md` (1.7 help-line update).

### 7d — STATUS + smoke playbook

**Purpose:** Reflect Phase 7 completion in tracker + manual playbook.

**Tasks:**

1. Update `STATUS.md`:
   - Phase 7 row: `Not started` → `Complete (2026-05-XX)`.
   - "Overall" paragraph: replace the now-stale Phase 6 "awaiting user merge" sentence with a Phase-7-aware summary. Add the queue-as-seam note.
   - LOC line: bump by the actual count (target: ≤ +250).
2. Append a Phase 7 section to [tests/manual/smoke_test.md](../tests/manual/smoke_test.md) with the scenarios in the testing plan below.

**Files modified:** `STATUS.md`, `tests/manual/smoke_test.md`.

## Final file tree after Phase 7

```text
src/ubongo/
  delivery/
    __init__.py                              (new)
    queue.py                                 (new — enqueue, dequeue_deliverable, mark_delivered, last_n, flush_delivered)
  repl.py                                    (modified — enqueue/dequeue around print, /queue, return delivery_token)
  oneshot.py                                 (modified — flush_delivered call after print)
  memory/vault.py                            (unchanged — listener fires later in the flow)
  events.py                                  (unchanged — before_send added by convention at dispatch site)
tests/
  test_delivery_queue.py                     (new — ~8 tests)
  test_delivery_path.py                      (new — ~4 tests on send-path event ordering, error path, enqueue failure)
  test_repl.py                               (modified — /queue tests, help-line update)
  test_repl_summary.py                       (verified unchanged)
Plans/
  phase-7-queue.md                           (new — this file)
STATUS.md                                    (modified)
tests/manual/smoke_test.md                   (modified — Phase 7 section + 1.7 help-line tweak)
```

Untouched: `agents/`, `classifier.py`, `context.py`, `llm.py`, `events.py` source, `memory/store.py`, `memory/compaction.py`, `skills.py`, `router.py`, `config/`.

## Testing plan

Manual smoke (appended as § Phase 7 in `tests/manual/smoke_test.md`):

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 7.1 | Queue contains the response | `rm -f data/ubongo.db`; `uv run python -m ubongo send "hello" --persona casual`; `sqlite3 data/ubongo.db "SELECT id, urgency, source, delivered_at IS NOT NULL AS delivered FROM notification_queue"` | one row with `urgency='urgent'`, `source='response'`, `delivered=1`. |
| 7.2 | `/queue` table | send 3 messages; in REPL `/queue` | header `Recent queue (last 10):` (or `last 3:` if cap-aware); 3 rows, newest first; each has id, two timestamps, urgency, source, preview. |
| 7.3 | `/queue N` argument | `/queue 1` after 3 sends | exactly one row (most recent). |
| 7.4 | Latency | `time uv run python -m ubongo send "hi" --persona casual` before/after this phase | indistinguishable (queue add ≪ LLM call). |
| 7.5 | `before_send` hook fires | `uv run python -c "from ubongo import events; events.register('before_send', lambda p: print('GOT', p['row_id'], file=__import__('sys').stderr)); from ubongo import oneshot; oneshot.run('hi', 'casual')"` | response on stdout; `GOT <id>` on stderr; vault entry present. |
| 7.6 | Vault still works | `rm -f vault/daily/$(date -u +%Y-%m-%d).md`; send one message | vault file recreated with the turn. Vault writes only on delivery success. |
| 7.7 | Error path enqueues too | `OPENROUTER_API_KEY=sk-or-v1-bogus uv run python -m ubongo send "hi" --persona casual`; query `notification_queue` | `source='error'` row present with `delivered_at NOT NULL`; stdout `Sorry, I couldn't reach the model. Check the logs.`; vault NOT written for this turn. |
| 7.8 | Pytest passes | `uv run pytest tests/` | all green (110 prior + ~12 new ≈ 122). |

## Out of scope for Phase 7 (do NOT build)

- Multi-transport. v0.1 stdout only; v0.2 Telegram will be a `before_send` consumer.
- `deliver_after` scheduling for proactive jobs. The column exists; the v0.1 send path always passes `None`. Proactive output is v0.3.
- `expires_at` enforcement beyond "skip in dequeue_deliverable". No reaper job.
- Background async worker, polling, or threadpool. Synchronous in-process round-trip only.
- Routing slash echoes ("Switched to casual.", "Unknown command: /foo", "Reloaded …") through the queue — see Q3 below.
- `/summary` going through the queue — see Q4 below.
- Per-row retry / poison handling. v0.1 dequeue + deliver is one round-trip.
- Crash recovery beyond what SQLite already gives us (atomic INSERTs; rows stay if process dies between print and `mark_delivered`).

## Open questions to confirm before I start

1. **`handle_text` returns a delivery token; print stays at the caller (recommended option A in 7b)?** Alternative: move `print()` into a delivery helper and pass a printer callable. I lean A — keeps REPL/oneshot imperative; smaller blast radius; existing test pattern (inspect returned text) keeps working. OK?
2. **Error responses go through the queue with `source='error'`?** They're outbound messages, so yes per invariant 9. Alternative: skip the queue on the error path. I lean route them — uniform contract, error-path visibility in `/queue`. OK?
3. **Slash echoes stay direct-print, NOT queued?** "Switched to casual.", "Unknown command: /foo", "Reloaded …" are REPL housekeeping, not LLM responses. Spec scenario 1 ("send a message; query notification_queue → row with delivered_at set") implies LLM responses only. Alternative: queue them as `source='ack'` — uniform but noisy. I lean don't queue. OK?
4. **`/summary` stays direct-print, NOT queued?** Phase 6 already chose "no persistence, no after_send" for it. Keeping it out of the queue keeps that promise. Alternative: queue it for uniformity. I lean keep out. OK?
5. **`after_send` semantic shift to "after stdout print succeeded".** Observable change: a `BrokenPipeError` on `print()` would skip the vault note (today it would have been written). Spec-pure (vault is a delivery side-effect) but a behavior change. Alternative: keep vault on LLM-success and add a separate `after_delivery` event. I lean the spec-pure move. OK?
6. **`/queue` default N = 10, accepts optional integer arg?** Or fixed 10? I lean optional, parsed as int, default 10. OK?
7. **`enqueue()` default `urgency='urgent'` for response path?** Per spec line 84 (`enqueue(content, urgency='urgent', source='response')`). I'll inherit.

If you don't push back on any, I'll go with the defaults above.

## Definition of done for Phase 7

- Four commits on `phase-7-queue` (7a, 7b, 7c, 7d).
- Smoke scenarios 7.1–7.7 pass; 7.8 pytest green.
- `tests/test_delivery_queue.py` (~8 tests) + `tests/test_delivery_path.py` (~4 tests) added; the prior 110 still pass.
- `tests/manual/smoke_test.md` Phase 7 section appended; scenario 1.7 help-line updated to include `/queue`.
- `STATUS.md` Phase 7 row → Complete; stale Phase 6 "awaiting user merge" line fixed; LOC count updated.
- Branch handed to you for merge. **Don't merge.**
