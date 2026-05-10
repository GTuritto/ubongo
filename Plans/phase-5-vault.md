# Phase 5 — Markdown Vault Projection: Implementation Plan

Date: 2026-05-10
Branch: `phase-5-vault` (off `main`)
Spec source: [UBONGO_BUILD.md](../UBONGO_BUILD.md) lines 813–839; named events 297–319.

## Goal

Each conversational turn (user + assistant pair) appends to an Obsidian-compatible Markdown daily note at `vault/daily/YYYY-MM-DD.md`. Read-only in v0.1; bidirectional sync is Phase 21. The vault writer subscribes to a new `after_send` event so memory write logic and vault projection stay decoupled (a tester can unregister the handler to prove independence — spec test 3).

## Why this plan exists

Phase 5 is small but introduces two patterns later phases inherit:
1. The `after_send` event becomes the natural seam for "post-turn side effects" — Phase 7's notification queue dispatches it after delivery, Phase 21 wires bidirectional sync onto it. Getting the payload shape right now means Phase 7 doesn't have to widen it.
2. `events.unregister()` is needed for spec test 3 ("unregister vault handler"); adding it now is one small function that Phases 8+ will use too.

## Branch + commit strategy

Branch already cut. Three commits:
- 5a: vault writer + tests + `.gitignore` tweak.
- 5b: `events.unregister` + `after_send` dispatch from `handle_text` + handler registration.
- final: STATUS + smoke playbook (with Obsidian rendering verified by-eye, sub-phase 5d).

## Sub-phases

### 5a — Vault writer

**Purpose:** A function `append_to_daily_note(date, user_message, response, persona, *, auto_routed=False)` that creates `vault/daily/<date>.md` lazily and appends a turn entry.

**Tasks:**

1. Create `src/ubongo/memory/vault.py`:
   - `_VAULT_DIR` from `settings.yaml`'s `vault.path` + `vault.daily_notes_subdir`.
   - `_daily_path(date: date) -> Path`.
   - `append_to_daily_note(date, time, user_message, response, persona, *, auto_routed=False) -> Path`:
     - Lazy-mkdir the daily dir.
     - If file doesn't exist: write a YAML frontmatter block + H1 date heading.
     - Append a turn entry with the time, persona, user message, and response.
2. Format (decided once, lock for v0.1):
   ```markdown
   ---
   date: 2026-05-10
   tags: [ubongo, daily]
   ---

   # 2026-05-10

   ## 14:25:30 — casual

   **You:**

   <verbatim user message>

   **Ubongo:**

   <verbatim assistant message>
   ```
   The `auto_routed=True` case appends ` (auto)` to the persona suffix in the H2 line.
3. Update `.gitignore`: replace `vault/` with `vault/*` + `!vault/.gitkeep` so the directory marker is tracked but generated notes stay ignored.
4. Add `vault/.gitkeep` (empty).

**Files added:** `src/ubongo/memory/vault.py`, `vault/.gitkeep`.
**Files modified:** `.gitignore`.

**Decision flagged:** User markdown is written verbatim with no escaping. If the user's message contains `##`, it appears as a real heading in the daily note — visible nesting, but no document corruption (the next turn's `## HH:MM:SS` heading still parses the same). Escaping would prevent legitimate markdown the user might type. Keep verbatim.

### 5b — Events unregister + `after_send` wiring

**Purpose:** Add `events.unregister(event, handler)` for clean handler removal. Wire `after_send` dispatch from `handle_text` after the assistant message is persisted. Register the vault writer as the default `after_send` handler.

**Tasks:**

1. Modify `src/ubongo/events.py`:
   - Add `unregister(event: str, handler: Handler) -> None`. Removes the first matching handler if present; silent no-op if absent.
2. Modify `src/ubongo/repl.py` (in `handle_text`):
   - After `store.append_message("assistant", ...)` lands and the session is updated, dispatch `after_send`:
     ```python
     events.dispatch("after_send", {
         "user_message": message,
         "response": text,
         "persona": chosen,
         "auto_routed": auto_mode,
         "conversation_id": conv_id,
         "user_message_id": user_msg_id,
         "assistant_message_id": assistant_msg_id,
         "ts": store.now_iso(),
     })
     ```
3. Add a default vault handler in `src/ubongo/memory/vault.py`:
   - `_after_send_handler(payload)`: extracts user/response/persona/ts, calls `append_to_daily_note(date.today(), datetime.now().time(), ...)`. Date and time derived from `ts` so `UBONGO_FAKE_NOW` flows through.
   - At module load: `events.register("after_send", _after_send_handler)`.
4. Update `src/ubongo/memory/__init__.py` to also import `vault` (so the handler registers when the memory package is imported).

**Files modified:** `src/ubongo/events.py`, `src/ubongo/repl.py`, `src/ubongo/memory/__init__.py`, `src/ubongo/memory/vault.py` (continuing 5a).

**Decision flagged:** `after_send` fires from `handle_text`, not from a queue worker. When Phase 7 lands the notification queue, the queue's delivery code becomes the new dispatch point and `handle_text`'s direct dispatch goes away. Phase 5's payload shape is forward-compatible.

**Decision flagged:** No vault entry on LLM error. If the LLM call fails, the polite stdout message is shown but no assistant message is persisted to the DB; we skip `after_send` for that turn. Vault stays a record of successful turns only. (Logs still capture the failure.)

### 5d — Obsidian compatibility check (manual, no code)

**Tasks:**

1. After 5a + 5b are committed and turns have written real entries, open `vault/` as an Obsidian vault on the desktop.
2. Verify: dailies render correctly; persona suffix readable; `**You:** / **Ubongo:**` bolds correctly; YAML frontmatter is parsed (visible as a properties panel).
3. Optionally check graph view — daily notes will be isolated until Phase 20 adds vault links.

**Files added/modified:** none (manual check).

## Final file tree after Phase 5

```text
src/ubongo/memory/
  vault.py            (new)
  __init__.py         (modified — imports vault so the handler registers)
src/ubongo/
  events.py           (modified — unregister)
  repl.py             (modified — dispatch after_send at end of handle_text)
vault/
  .gitkeep            (new, tracked)
  daily/<date>.md     (generated; gitignored)
.gitignore            (modified — vault/* + !vault/.gitkeep)
tests/
  test_vault.py       (new)
Plans/
  phase-5-vault.md    (new — this file)
STATUS.md             (modified)
tests/manual/smoke_test.md (modified)
```

Untouched: classifier, router, llm, agents/, governance/, evolution/.

## Testing plan

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Daily note write | Send 3 messages via `ubongo send` | `vault/daily/<today>.md` exists with frontmatter, H1 date, three `## HH:MM:SS — <persona>` sections containing the user/assistant pairs verbatim. |
| 2 | Obsidian render (manual) | `open -a Obsidian vault/` (or open in Obsidian app) | Frontmatter shows in properties panel; H1/H2 hierarchy correct; bold labels render; line wraps cleanly. |
| 3 | Handler disable | Run a small Python snippet that imports `vault`, calls `events.unregister("after_send", vault._after_send_handler)`, sends a message via `oneshot.run`, then checks the daily note size — should not have grown. SQLite still has the new messages. | Vault file size unchanged; `messages` table has the new rows. |
| 4 | Date rollover | `UBONGO_FAKE_NOW=<date_X>T10:00:00+00:00 ubongo send "..."`; then `UBONGO_FAKE_NOW=<date_X+1>T10:00:00+00:00 ubongo send "..."` | Two daily files exist: `<date_X>.md` and `<date_X+1>.md`. |

Plus pytest:

| # | Pytest | Expected |
| --- | --- | --- |
| pytest | New `test_vault.py` (~6 tests). Existing 69 tests still pass. ~75 total. | All pass. |

`test_vault.py` uses `tmp_path` to override the vault directory; covers first-write file creation with frontmatter, append-on-second-write, persona auto-suffix, multiple-turns-same-day, date-rollover separate files, and `events.unregister` removing the handler.

## Smoke playbook updates

Append a Phase 5 section with the 4 scenarios above plus a pytest line.

## Out of scope for Phase 5 (do NOT build)

- Bidirectional sync, file-watcher ingestion (Phase 21).
- Vault links / graph (Phase 20).
- Embeddings (Phase 20).
- Skill bodies as vault content (Phase 6 ships skills but they live under `config/skills/`, not the vault).
- Note refactoring / merging — append-only writes for v0.1.
- Multi-vault support — single vault path from settings.yaml.

## Open questions to confirm before I start

1. **Daily note format — frontmatter + H1 + H2 entries (recommended)?** The format I proposed above. Alternative: no frontmatter, just H1 + H2. Frontmatter buys you Obsidian properties / tag indexing; cost is 4 lines per file. I lean keep it.
2. **`(auto)` suffix on the persona line for auto-routed turns?** `## 14:25:30 — casual (auto)` so you can scan a daily note and see which turns the classifier picked vs which were manual. OK?
3. **Skip vault writes on LLM error (recommended)?** When the model call fails and the user sees the polite "Sorry…" message, no DB row is written and no vault entry. Alternative: write a `[error]` marker entry. I lean skip — the vault is a record of conversation, errors are operational and live in logs.
4. **`.gitignore` change to `vault/*` + `!vault/.gitkeep`?** Standard pattern for "track the directory marker, ignore contents." Override?

If you don't push back, I'll go with the defaults above.

## Definition of done for Phase 5

- Three commits on `phase-5-vault` (5a, 5b, final).
- All 4 manual smoke scenarios pass (test 2 is by-eye in Obsidian, the rest are scriptable).
- New pytest for vault writer; existing tests still pass.
- `tests/manual/smoke_test.md` Phase 5 section populated.
- `STATUS.md` Phase 5 row → Complete; LOC count updated.
- Branch handed to you for merge. Don't merge.
