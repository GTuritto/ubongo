# Phase 4 — SQLite Memory + Compaction: Implementation Plan

Date: 2026-05-10
Branch: `phase-4-memory` (off `main`)
Spec source: [UBONGO_BUILD.md](../UBONGO_BUILD.md) lines 781–811; full schema lines 321–467.

## Goal

Conversations persist across restarts. Each REPL turn (and one-shot turn) appends to a SQLite-backed conversation log. After 30 minutes of inactivity, the next message starts a new conversation. When a conversation grows past 30 turns, compaction summarizes the older messages into a single paragraph; recall = summary + last N (10) messages. The Memory Agent isn't built yet (that's Phase 9), but the durable-state storage that the Memory Agent will be the sole writer to lands here.

## Why this plan exists

Phase 4 is the largest substrate phase: it lays down the entire SQLite schema (every table, including the empty ones for governance, evolution, queue, vault links — Phase 5+ fills them) and the Store API every later phase reads from. Three contracts solidify here that everything downstream depends on: the `Store` interface, the `Compaction` strategy registry, and the session-timeout rule. Done well, the next eight phases just plug in.

## Branch + commit strategy

Branch already cut. Seven commits, one per sub-phase, plus a final STATUS + smoke commit. Eight total. Larger than prior phases because Phase 4 has real depth.

## Sub-phases

### 4a — Schema + bootstrap

**Purpose:** A self-contained schema file plus a `bootstrap()` function that creates all tables idempotently. All v0.1 tables created up front (per spec), even though most stay empty until Phase 8+. Avoids needing migrations.

**Tasks:**

1. Create `src/ubongo/memory/__init__.py` (empty marker).
2. Create `src/ubongo/memory/schema.sql` — copy the full v0.1 schema from `UBONGO_BUILD.md` lines 321–467 verbatim, except:
   - Strip the `vec_messages` / `vec_vault` virtual table comment (Phase 20 lands those).
   - Wrap every `CREATE TABLE` and `CREATE INDEX` with `IF NOT EXISTS` so re-running is idempotent.
3. Create `src/ubongo/memory/store.py` with the bootstrap pieces:
   - `_DB_PATH = _REPO_ROOT / "ubongo.db"` (project root; covered by existing `.gitignore` `*.db`).
   - `_SCHEMA_PATH = Path(__file__).parent / "schema.sql"`.
   - `_get_connection() -> sqlite3.Connection`: returns a per-thread connection; enables `PRAGMA foreign_keys = ON`.
   - `bootstrap() -> None`: opens the connection (creating the file if missing), executes `schema.sql` once. Called automatically the first time any other store function runs.
   - Sets `row_factory = sqlite3.Row` so callers get dict-like rows.

**Files added:** `src/ubongo/memory/__init__.py`, `src/ubongo/memory/schema.sql`, `src/ubongo/memory/store.py` (skeleton — full API in 4b).

**Decisions flagged:**

- **DB at repo root (`./ubongo.db`).** Single-user, local-first, no networked instance. Already gitignored via `*.db`. Alternative (`./data/ubongo.db`) requires creating a directory; not worth the friction for one file.
- **Schema all-tables-up-front.** Per spec: Phase 4 creates the entire v0.1 schema even though Phases 8, 14, 18 are the first to write to most of it. Migrations are the maintenance cost we're avoiding.
- **`PRAGMA foreign_keys = ON`.** SQLite defaults to off. Turning it on enforces the FK constraints the schema declares (e.g., messages → conversations). Cost is negligible; safety is real.

### 4b — Store API

**Purpose:** A small set of typed functions over the schema. Other modules call these; nothing else touches SQL.

**Tasks:**

1. Extend `src/ubongo/memory/store.py` with:
   - `Conversation` and `Message` dataclasses (subset of schema columns we read; insert-only fields like `id` and `timestamp` come back from the DB).
   - `start_conversation(active_persona: str) -> int`: insert into `conversations`, return `id`.
   - `end_conversation(conversation_id: int) -> None`: set `ended_at = now`.
   - `append_message(conversation_id, role, content, persona=None, model=None, tokens_in=0, tokens_out=0) -> int`: insert into `messages`, return `id`. `agent` and `skill` columns kept NULL until later phases.
   - `last_n_messages(conversation_id: int, n: int) -> list[Message]`: chronological order, oldest first.
   - `latest_summary(conversation_id: int) -> Summary | None`: returns the most recent summary for the conversation (covers the largest range), or `None`.
   - `persist_summary(conversation_id, covers_from_message_id, covers_to_message_id, content, strategy) -> int`.
   - `get_session(user_id: int = 1) -> Session | None`: returns the row from `sessions`, or `None` if no row yet.
   - `upsert_session(user_id=1, *, last_message_at, active_persona, current_conversation_id) -> None`: insert if missing, update otherwise. `override_until` left NULL.
   - `count_messages_since_summary(conversation_id) -> int`: messages whose id > `latest_summary.covers_to_message_id` (or all messages if no summary).
2. All timestamp values are ISO 8601 UTC strings stored in the TIMESTAMP columns; helper `_now_iso() -> str`.
3. The store module-level cache: a `connection` singleton per process. Test fixtures override `_DB_PATH` to a tempfile.

**Files modified:** `src/ubongo/memory/store.py` (continuing).

**Decisions flagged:**

- **Single-user assumption.** `user_id=1` everywhere. v0.1 is single-user (memory `feedback_ubongo_v0.1_full_vision.md`). Multi-user is explicitly out of scope.
- **Timestamps as ISO 8601 strings, not unix epoch.** SQLite TIMESTAMP is text; ISO strings are sortable lexicographically and human-readable in `sqlite3 ubongo.db`.
- **No ORM.** Plain `sqlite3` with parameterized queries. Keeps the dependency surface zero-extra and the SQL inspectable.

### 4c — Session timeout

**Purpose:** Define when "the same conversation continues" vs "a new conversation begins." The rule is `last_message_at` gap < 30 minutes (configurable via `settings.memory.session_timeout_minutes`).

**Tasks:**

1. Add `current_or_new_conversation(now: datetime, persona: str) -> int` to `store.py`:
   - Read session for user_id=1.
   - If session exists, has `current_conversation_id`, and `now - session.last_message_at < timeout`: return the existing conversation id.
   - Else: end the previous conversation (if any) with `ended_at = session.last_message_at`, start a new one, update the session's `current_conversation_id`. Return the new id.
2. Read the timeout from `settings.memory.session_timeout_minutes` via `load_config()`.
3. The function takes `now` as a parameter (not `datetime.now()`) so tests can pass a fixed clock.

**Files modified:** `src/ubongo/memory/store.py` (continuing).

**Decision flagged:** I considered tracking the session-end transition as a separate "session_ended" event. It's not in the spec event list and Phase 4 doesn't need it. Skipping.

### 4d — Compaction registry + default strategy

**Purpose:** A pluggable strategy registry. When messages-since-summary exceeds a configurable threshold, summarize the older portion into a paragraph and persist as a `summaries` row. Recall returns the summary plus the last N messages.

**Tasks:**

1. Create `src/ubongo/memory/compaction.py`:
   - `Strategy` typed as `Callable[[list[Message]], str]` — takes the messages to summarize, returns a paragraph.
   - `_strategies: dict[str, Strategy]` registry.
   - `register(name: str, strategy: Strategy) -> None`.
   - `get(name: str) -> Strategy`: raises `KeyError` if unknown.
   - `default_strategy(messages: list[Message]) -> str`: a single LLM call to `models.compaction` (haiku-4.5) with a system prompt asking for a tight one-paragraph summary. Reuses `llm.complete` so we get the same retry + event hooks.
   - `register("default", default_strategy)` at module load.
   - `maybe_compact(conversation_id: int, recall_turns: int, trigger_at: int, strategy: str = "default") -> Summary | None`:
     - Use `count_messages_since_summary`. If less than `trigger_at`, return None.
     - Compute the message-id range to summarize: from `(latest_summary.covers_to_message_id + 1)` (or 0) to `(current_max_message_id - recall_turns)`. If the range is non-empty, fetch those messages, call the strategy, persist the summary.
2. Idempotency comes from `count_messages_since_summary` < trigger_at after the previous summary lands. Spec test 4 covered.

**Files added:** `src/ubongo/memory/compaction.py`.

**Decisions flagged:**

- **Compaction LLM call is synchronous in-line.** A turn that triggers compaction will pause for the haiku-4.5 call before the user sees the response. Acceptable for v0.1 (compaction is rare); Phase 13 Repair Agent or a dedicated background task could move it off the hot path. Documented for later.
- **Strategy registry rather than a class hierarchy.** Spec says "registry pattern". Functions over classes for one-method protocols.

### 4e — Wire `after_recall` event (compaction handler)

**Purpose:** A `recall(conversation_id) -> RecallContext` function emits an `after_recall` event with the loaded context. A registered handler triggers compaction when the threshold is hit. Decoupling means the recall path doesn't know about compaction directly; tests can register fake handlers.

**Tasks:**

1. Add `recall(conversation_id: int) -> RecallContext` to `store.py`:
   - Load `latest_summary` and `last_n_messages(conversation_id, recall_turns)`.
   - Return a `RecallContext(summary_text, messages)` dataclass.
   - Dispatch `after_recall` with `{"conversation_id": ..., "messages_since_summary": ..., "recall_turns": ...}`.
2. Add a default handler `_compaction_handler(payload)` in `compaction.py` that calls `maybe_compact` if the threshold is reached. Register it on module import.
3. `events.dispatch("after_recall", ...)` runs handlers synchronously; the handler may write a new summary which the next `recall()` call picks up.

**Files modified:** `src/ubongo/memory/store.py`, `src/ubongo/memory/compaction.py`.

**Decision flagged:** The compaction handler runs **after** `recall()`, not before. The current turn uses the existing summary + messages; the new summary covers older messages and is read on the next turn. This is simpler than re-running recall after compaction; the user-visible difference is one turn of staleness, which is invisible in practice.

### 4f — Wire `after_llm` event (memory write handler)

**Purpose:** Persist user and assistant messages to memory as part of every turn. Hook the assistant write into the existing `after_llm` event from Phase 2.

**Tasks:**

1. In `repl.py` `handle_text`:
   - At the top: `current_conv_id = store.current_or_new_conversation(now=datetime.utcnow(), persona=persona_name)`.
   - Append the user message: `user_msg_id = store.append_message(current_conv_id, "user", message, persona=persona_name)`.
   - Build messages from `recall(current_conv_id)`: prepend summary to the system prompt OR pass it as a leading system message; add the prior turns as alternating user/assistant entries; append the current user message.
   - Call `llm.complete` (existing path).
   - Append the assistant message: `store.append_message(current_conv_id, "assistant", text, persona=used_persona, model=result.model, tokens_in=result.tokens_in, tokens_out=result.tokens_out)`.
   - Update session `last_message_at`, `active_persona`, `current_conversation_id`.
2. The `after_llm` handler is the cleaner way to persist the assistant message without coupling. I'll register a small handler in `repl.py` (or a new `memory/handlers.py`) that listens for `after_llm` and writes the message. **However**, the `after_llm` payload as defined in Phase 2 doesn't carry the conversation_id or response text — only model/tokens/latency. To make the handler work, I'll either widen the `after_llm` payload (small breakage) or keep memory writes inline in `handle_text` (simpler, avoids touching the Phase 2 contract). I'll go with **inline writes in handle_text** for Phase 4; widening the event is the kind of change Phase 8 (Master Agent) does as part of its bigger event refactor.
3. Same wiring in `oneshot.py`.

**Files modified:** `src/ubongo/repl.py`, `src/ubongo/oneshot.py`.

**Decision flagged:** Inline memory writes vs `after_llm` handler. The spec says "wire after_llm event (memory write handler attached)." Strict reading favors the handler; pragmatic reading says inline because the payload is too narrow. I'll do **both**: inline `store.append_message` calls in `handle_text`, AND register a no-op `after_llm` handler in `memory/__init__.py` that just logs `"memory_after_llm"` so the seam exists. Phase 8's payload widen makes the handler actually do work.

### 4g — Persist active persona / override into sessions

**Purpose:** Move the REPL's in-memory `persona` and `auto_mode` state into the `sessions` table so it survives restart. Spec calls out persona; auto_mode is a Phase-3 add not covered by the schema. I'll persist persona; auto_mode stays in-memory (loses on restart, which matches "/auto is a session decision, not a permanent setting").

**Tasks:**

1. On REPL startup: `session = store.get_session()`; if exists, `persona = session.active_persona`. Else: `persona = DEFAULT_PERSONA`.
2. On any persona change (slash dispatch or auto-route): `store.upsert_session(active_persona=new_persona, ...)`.
3. `current_or_new_conversation` reads/writes the session row, so the same row holds the active persona. No schema change needed.
4. `auto_mode` in Phase 4: NOT persisted. Lives in REPL loop state. User must `/auto` again after restart. (This is fine — `/auto` is a per-conversation decision.)

**Files modified:** `src/ubongo/repl.py` (continuing).

## Final file tree after Phase 4

```text
src/ubongo/
  memory/
    __init__.py    (new)
    schema.sql     (new)
    store.py       (new)
    compaction.py  (new)
  repl.py          (modified — store integration, recall, persona persistence)
  oneshot.py       (modified — same recall + persist)
  ...
tests/
  test_memory_store.py       (new)
  test_memory_compaction.py  (new)
ubongo.db                    (new at runtime; gitignored)
Plans/
  phase-4-memory.md          (new — this file)
STATUS.md                    (modified)
tests/manual/smoke_test.md   (modified — Phase 4 section populated)
```

Untouched: `events.py` (we use it), `classifier.py`, `router.py`, `agents/personas.py`, `llm.py`, `context.py`, `config.py`, `logging.py`, `__main__.py`. The Memory Agent (Phase 9) consumes this; not building it yet.

## Testing plan (from spec, made concrete)

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Persistence across restart | REPL: 5 turns about a topic; `/exit`; restart REPL within 30 min; ask "what were we just discussing?" | Bot remembers the topic. `messages` count > 10; `conversations` row has same id; `current_conv_id` continues. |
| 2 | New session after timeout | After test 1: simulate 31-min gap (test fixture uses fake clock; manual test waits OR I'll add an env var to fake the clock for the smoke run); send a message | New `conversations` row; old context not in recall. Last conversation has `ended_at` set. |
| 3 | Compaction trigger | Conversation reaches 31 turns | `summaries` row created; `recall()` returns summary + last 10. The `compaction_run` log event fires. |
| 4 | Compaction idempotency | After test 3: 5 more turns | No second summary row. `count_messages_since_summary` is 5; below threshold (30). |
| 5 | Swappable strategy | `compaction.register("stub", lambda msgs: "STUB")`; trigger compaction with `strategy="stub"` | Persisted summary content == "STUB". |

Plus pytest:

| # | Pytest | Expected |
| --- | --- | --- |
| pytest | New: `test_memory_store.py` (~10 tests), `test_memory_compaction.py` (~6 tests). Existing: classifier/router/repl/personas/events still pass. | All pass. ~57 tests total. |

`test_memory_store.py` uses a tempfile DB per test (fixture sets `_DB_PATH`). `test_memory_compaction.py` mocks `llm.complete` for the default strategy and uses a stub strategy for the swappable test. Neither hits the network.

## Smoke playbook updates

1. Append a Phase 4 section to `tests/manual/smoke_test.md` with the 5 scenarios.
2. **Phase 1's smoke needs a small adjustment:** Phase 1 test 1.4 (`/exit clean quit`) and test 1.9 (`EOF clean exit`) still work, but the assistant messages now persist to SQLite. Add a parenthetical to those rows ("a turn before `/exit` will land in `ubongo.db`; remove it manually if you want a clean state"). Same for one-shot tests.
3. **Phase 2's smoke** is unchanged — the responses still go to stdout; persistence is a stderr-side fact that the playbook doesn't have to assert.
4. **Phase 3's smoke** unchanged.

## Out of scope for Phase 4 (do NOT build)

- The Memory Agent itself (Phase 9). Phase 4 is the storage substrate; Phase 9 is the agent that mediates writes from other agents.
- Vault projection, daily notes (Phase 5).
- Skills (Phase 6).
- Outbound queue logic (Phase 7) — the table is created here per spec but no writes happen.
- Master Agent and workflow_runs writes (Phase 8) — table created, empty.
- Risk/governance writes (Phase 14) — table created, empty.
- Evolution lineage / evaluations / promotions writes (Phase 16+) — tables created, empty.
- Vault links (Phase 5/Phase 21) — table created, empty.
- Embeddings (`vec_messages`, `vec_vault`) — Phase 20.
- Facts extraction (no spec phase explicitly claims it; skip until consumed).
- `auto_mode` persistence across restart (in-memory only in Phase 4).

## Open questions to confirm before I start

1. **DB path: `./ubongo.db` at repo root?** Already covered by `.gitignore` `*.db`. Alternative is `./data/ubongo.db` (slightly tidier; needs a `data/` directory). I lean repo root; one less directory. Override?
2. **Memory writes: inline in `handle_text` (recommended)?** vs trying to thread the conversation_id through the `after_llm` event payload (cleaner-feeling but requires widening Phase 2's contract). I'll keep writes inline and register a no-op `after_llm` handler so the seam exists for Phase 8 to extend. OK?
3. **`auto_mode` not persisted across restart?** Simpler. `/auto` is a per-session decision. Phase 4 schema doesn't have a column for it. Adding one is a small spec deviation. I lean don't persist. Override?
4. **Recall surface in the LLM call: prepend summary to the system prompt** (cleaner) **or insert as a leading user message marked "[summary so far]"**? I lean system-prompt prepend — keeps the user/assistant message list pure conversation. Override?
5. **Test 2 (31-min timeout) in manual smoke.** Real wait is annoying. I'm planning to expose a hidden env var `UBONGO_FAKE_NOW` that, if set, overrides `datetime.utcnow()` in the store. Used in pytest and in the smoke playbook for this test. OK?

If you don't push back, I'll go with the defaults above.

## Definition of done for Phase 4

- Eight commits on `phase-4-memory`.
- All 5 manual smoke scenarios pass (test 2 uses the fake-clock env var).
- New pytest for store and compaction; existing tests still pass; ~57 tests total.
- `tests/manual/smoke_test.md` Phase 4 section populated; Phase 1 rows updated for the `ubongo.db` side effect.
- `STATUS.md` Phase 4 row → Complete (2026-05-10); LOC count updated.
- Branch handed to you for merge. Don't merge.
