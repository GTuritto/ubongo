# Phase 6 — Skills + Progressive Disclosure: Implementation Plan

Date: 2026-05-11
Branch: `phase-6-skills` (off `main`)
Spec source: [UBONGO_BUILD.md](../UBONGO_BUILD.md) lines 841–870; file-structure 189–197; reversibility note 150.

## Goal

Skills become first-class config-as-folder objects. At startup, Ubongo scans `config/skills/`, parses each `SKILL.md`'s YAML frontmatter, and builds a registry of `{name, description, risk, reversibility, ...}`. Bodies and per-skill prompt files stay on disk and only load on first activation (then cache; `/reload` clears the cache). v0.1 ships one skill, `summarize-conversation`, invoked via `/summary`. The classifier is upgraded to suggest a skill, and the REPL gains `/skills`, `/skill <name>`, `/summary`, and `/reload` commands.

## Why this plan exists

Phase 6 introduces three patterns later phases inherit:

1. **Registry + lazy body** is the template for every config-as-folder asset (workflows in Phase 9, constrained-bash skill in Phase 11, governance-relevant skill fields in Phases 14–15). Getting the loader and frontmatter shape right now saves a refactor.
2. **Classifier returns a real `suggested_skill`** (today it always returns `null`). Phase 8's Master Agent uses this directly to pick workflows; Phase 14's decision matrix consults the suggested skill's `risk` + `reversibility` fields. The fields we define here are load-bearing.
3. **`/reload` becomes the user-facing knob for hot-edits.** Today only `context.py` and `personas.py` have caches. Phase 6 adds a third (`skills.py`). Phase 9+ will keep adding caches; a single `/reload` that clears all of them is the contract.

## Branch + commit strategy

Branch already needs cutting (`phase-6-skills` off `main`). Five commits:

- **6a** — `skills.py` registry + frontmatter parsing + tests.
- **6b** — lazy body / prompt loading + `reload()` + tests.
- **6c** — classifier prompt + suggestion validation + tests.
- **6d/6e** — `summarize-conversation` skill files + `/summary` slash handler + REPL wiring (resolution order) + tests.
- **6f** — `/skills` and `/reload` REPL commands; STATUS + smoke playbook.

(If 6d/6e gets large I'll split into two; default is one commit.)

## Sub-phases

### 6a — Skill discovery + registry

**Purpose:** A registry that knows the *name* and *description* of every skill at startup, without reading bodies.

**Tasks:**

1. Create `src/ubongo/skills.py`:
   - `@dataclass(frozen=True) class Skill`: `name: str`, `description: str`, `risk: str`, `reversibility: str`, `default_persona: str | None`, `prompts: dict[str, str]`, `dir: Path`.
   - `_split_frontmatter(text)` — reused pattern from `agents/personas.py` (lift to a shared helper later; for Phase 6 just copy, it's 8 lines).
   - `_discover() -> dict[str, Skill]`: scan `config/skills/*/SKILL.md`; parse frontmatter only (use `_split_frontmatter` then discard the body); validate required fields; return registry. Missing required fields → raise `ValueError` with a clear message naming the skill dir.
   - `list_skills() -> list[Skill]`: returns registry values, name-sorted.
   - `get(name) -> Skill`: registry lookup; raises `KeyError`.
   - `_registry: dict[str, Skill] | None = None`; lazy-built on first access via `_ensure()`.
2. Frontmatter schema for v0.1 (decision flagged below):
   - **Required:** `name` (str), `description` (str), `risk` ∈ {`low`, `medium`, `high`, `destructive`}, `reversibility` ∈ {`reversible`, `irreversible`}.
   - **Optional:** `default_persona` ∈ {`architect`, `operator`, `casual`}, `prompts` (mapping of `key -> relative path under skill dir`).
   - Unknown keys are ignored (forward-compat with Phase 11/14 fields).
3. Validate `risk` and `reversibility` against allowed values; reject unknown values with a clear error.

**Files added:** `src/ubongo/skills.py`, `tests/test_skills_discovery.py`.

### 6b — Lazy body + prompt loading

**Purpose:** Read the SKILL.md body and any `prompts/*.md` files only when needed; cache per process; clear on `reload()`.

**Tasks:**

1. In `src/ubongo/skills.py`:
   - `body(name) -> str`: reads `<skill.dir>/SKILL.md`, strips frontmatter, caches in `_body_cache`. Subsequent calls return the cached body.
   - `prompt(name, key) -> str`: looks up `skill.prompts[key]`, reads the file relative to `skill.dir`, caches in `_prompt_cache` keyed by `(name, key)`. Raises `KeyError` if the prompt key isn't declared.
   - `reload() -> None`: clears `_registry`, `_body_cache`, `_prompt_cache`; next access re-discovers.
2. Add a debug-level log line in `body()` and `prompt()` on cache miss: `skill_body_loaded` / `skill_prompt_loaded` with `{"name": ..., "key": ...}`. This is what spec test 4 ("body lazy-load") greps for.
3. Tests in `test_skills_lazy.py`: cache miss logs on first call, no log on second; `reload()` clears; missing prompt key raises; non-existent skill name raises.

**Files modified:** `src/ubongo/skills.py` (continuing 6a).
**Files added:** `tests/test_skills_lazy.py`.

### 6c — Classifier skill suggestion

**Purpose:** The classifier currently emits `suggested_skill: null` always. Wire it to consider registered skills and validate the result.

**Tasks:**

1. Modify `src/ubongo/classifier.py`:
   - Build the system prompt dynamically: pull `skills.list_skills()` and inject a `## Available skills` block listing `name — description` for each (one line per skill). If the registry is empty, omit the section and the prompt instructs `suggested_skill: null`.
   - Update the prompt text so `suggested_skill` is `one of the listed skill names, or null` (not hard-coded null).
   - In `_validate`, accept `suggested_skill: str | None`; reject any string that isn't a registered skill name (treat as null + log warning `classify_unknown_skill`). This prevents hallucinated skill names from poisoning routing in Phase 8.
2. Keep the classifier fallback path unchanged (still returns `suggested_skill=None`).
3. Tests in `test_classifier.py` (extend existing): mocked LLM returns a valid skill name → validated through; mocked LLM returns an unknown skill name → coerced to None with warning; system prompt contains the skill block when the registry is non-empty.

**Files modified:** `src/ubongo/classifier.py`, `tests/test_classifier.py` (existing — extend).

**Decision flagged:** the classifier sees `name — description`. Not the body. That's the whole point of progressive disclosure — descriptions are the index, bodies are the activation prompt. This matches spec 6c verbatim ("Pass list of skill names + descriptions").

### 6d — Skill resolution order

**Purpose:** Decide which skill (if any) applies to the current turn.

**Tasks:**

1. Add `src/ubongo/skills.py` helper `resolve(*, explicit: str | None, pinned: str | None, suggested: str | None) -> Skill | None`:
   - Returns the first non-None of `explicit`, `pinned`, `suggested`, validated against the registry.
   - Invalid names → log warning, fall through to the next layer.
2. REPL holds a `pending_skill: str | None` slot (one-shot, not sticky — cleared after the next turn consumes it).
3. `/skill <name>` slash command sets `pending_skill`; prints `Next turn will use skill: <name>.` or `Unknown skill: <name>.` on miss.
4. `/summary` is a shortcut for `pending_skill = "summarize-conversation"` AND triggers the turn immediately with an empty user message slot — actually cleaner: `/summary` calls a dedicated `run_summary_skill()` path (see 6e) that does not go through the standard text turn. Skill resolution logic only applies to plain-text turns.

**Resolution precedence (proposed, decision flagged):**

1. `pending_skill` (set by `/skill <name>` on a prior turn).
2. Classifier's `suggested_skill` (only when `auto_mode=True`).
3. None.

Direct skill slash commands (`/summary`) bypass this entirely — they invoke the skill immediately rather than annotating the next text turn.

**Files modified:** `src/ubongo/skills.py` (resolve), `src/ubongo/repl.py` (pending slot + `/skill`).

### 6e — `summarize-conversation` skill

**Purpose:** Ship the first real skill end-to-end.

**Tasks:**

1. Create `config/skills/summarize-conversation/SKILL.md`:
   ```markdown
   ---
   name: summarize-conversation
   description: Recap the recent conversation in 3-5 sentences. Use when the user wants to wrap up, get a recap, or ask "what did we just talk about".
   risk: low
   reversibility: reversible
   default_persona: operator
   prompts:
     summarize: prompts/summarize.md
   ---

   The summarize-conversation skill condenses the last N turns of the active conversation into a short, operator-voice recap. It is read-only over conversation memory; it does not write any new turn into the conversation log or vault. Use it as a meta-command, not as a regular turn.
   ```
2. Create `config/skills/summarize-conversation/prompts/summarize.md`:
   ```markdown
   ## Task

   Summarize the following conversation in 3 to 5 sentences. Capture the main topic, the key decisions or conclusions reached, and any open questions. Use the operator voice: direct, no padding, no preamble. Do not address the user. Do not invent details that aren't in the transcript.

   ## Conversation

   {transcript}
   ```
   (`{transcript}` is the only template variable; rendered by the slash handler.)
3. Add `src/ubongo/repl.py` handler `_run_summary()`:
   - Pull `current_or_new_conversation` and read the last `memory.recall_turns` messages via `store.last_n_messages`.
   - If fewer than 2 messages exist (nothing meaningful to summarize), print `Not enough conversation yet to summarize.` and return.
   - Render `{transcript}` as `User: ...` / `Ubongo: ...` lines, in chronological order.
   - Build the prompt via `context.build_system_prompt(persona="operator", skill="summarize-conversation")` — but **stop**: `build_system_prompt` currently reads the *whole* SKILL.md (frontmatter stripped). That includes the activation body, which isn't the right thing for `prompts/summarize.md`. See decision flagged below.
   - Call `llm.complete(system_prompt, [{role: "user", content: rendered_prompt}], persona.model, persona.max_tokens)`.
   - Print the response. **Do not** call `store.append_message`. **Do not** dispatch `after_send`. Do not write to vault. This is a meta-command and stays out of memory.
4. `/summary` slash command (in `handle_slash` or a new branch) → call `_run_summary()`. Returns no persona change.

**Files added:** `config/skills/summarize-conversation/SKILL.md`, `config/skills/summarize-conversation/prompts/summarize.md`.
**Files modified:** `src/ubongo/repl.py`.

**Decision flagged:** `build_system_prompt` vs skill activation prompt. Today `build_system_prompt(persona, skill=...)` appends the SKILL.md body to the system prompt. That body is *describing* the skill, not *instructing* the model. For `summarize-conversation`, the actual instruction lives in `prompts/summarize.md`. Two options:

- **(A) Recommended:** keep `build_system_prompt` semantics. SKILL.md body becomes a "you have access to this skill; here's what it does" preamble. The real instruction (from `prompts/summarize.md`) is rendered into the *user message* by the slash handler. This matches how Claude Code skills work and keeps system prompts persona-flavored.
- **(B)** treat `prompts/summarize.md` as the system prompt and discard SKILL.md body for activation. Simpler but loses the persona voice.

I lean (A). The persona stays in charge of voice; the skill provides the task. SKILL.md's body is short and useful context.

### 6f — `/skills` and `/reload` REPL commands

**Purpose:** Inspection and hot-reload knobs.

**Tasks:**

1. `/skills` slash command: print a small table — `name` / `description` / `risk` / `reversibility` columns. One row per registered skill. If empty: `No skills registered.`
2. `/reload` slash command: call `context.reload()`, `personas.reload()`, `skills.reload()`. Print `Reloaded UBONGO.md, personas, and skills.`
3. Extend `handle_slash` to recognize `/skill`, `/skills`, `/reload`, `/summary` and update the unknown-command help line accordingly.
4. Update [tests/manual/smoke_test.md](../tests/manual/smoke_test.md) with a Phase 6 section (5 scenarios + pytest).
5. Update [STATUS.md](../STATUS.md): Phase 6 row → Complete + LOC count; correct the stale "awaiting user merge" sentence in the Overall paragraph from Phase 5.

**Files modified:** `src/ubongo/repl.py`, `tests/manual/smoke_test.md`, `STATUS.md`.

## Final file tree after Phase 6

```text
src/ubongo/
  skills.py                                 (new)
  classifier.py                             (modified — skill block + validation)
  repl.py                                   (modified — /skill, /skills, /summary, /reload, pending slot)
config/skills/
  summarize-conversation/
    SKILL.md                                (new)
    prompts/
      summarize.md                          (new)
tests/
  test_skills_discovery.py                  (new)
  test_skills_lazy.py                       (new)
  test_skills_resolve.py                    (new)
  test_classifier.py                        (modified — skill block + validation)
  test_repl_summary.py                      (new)
Plans/
  phase-6-skills.md                         (new — this file)
STATUS.md                                   (modified)
tests/manual/smoke_test.md                  (modified)
```

Untouched: memory/, agents/, vault, events, llm, oneshot, config, context (no body-loading changes — skills.py owns its own cache).

## Testing plan

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | `/summary` works | Send 5 messages on a fresh conversation, then `/summary`. | Coherent 3–5 sentence operator-voice summary printed to stdout; no new `messages` row written for the summary; no new vault turn. |
| 2 | `/skills` lists registered | `/skills` after startup. | Table with one row: `summarize-conversation`. |
| 3 | `/reload` picks up edits | Edit `config/skills/summarize-conversation/prompts/summarize.md` (e.g., add "in haiku form"); `/reload`; `/summary`. | New body in effect — summary reflects the edit. |
| 4 | Body lazy-load | Start REPL with `--log-level=debug`; grep logs at startup vs after `/summary`. | No `skill_body_loaded` / `skill_prompt_loaded` lines at startup; both appear after `/summary`. |
| 5 | Classifier suggestion | In `/auto` mode, type "can you wrap this up for me". | Log line shows `suggested_skill=summarize-conversation`; classifier output validates. (Application is gated by 6d resolution rules — does not auto-run; the line is for inspection.) |
| 6 | Unknown skill is coerced | Mock the classifier to return `suggested_skill="not-a-real-skill"`. | Validated through to `None`; warning logged; turn proceeds normally. |
| 7 | `/skill <name>` one-shot | `/skill summarize-conversation`; then send "what's the weather like in Rome". | Confirmation printed; next turn runs with the skill applied via 6d resolution path (skill body in system prompt); turn AFTER that does not. (Decision flagged below.) |

Plus pytest:

| # | Pytest | Expected |
| --- | --- | --- |
| pytest | New files: `test_skills_discovery.py` (~6 tests), `test_skills_lazy.py` (~5 tests), `test_skills_resolve.py` (~4 tests), `test_repl_summary.py` (~4 tests). Extensions to `test_classifier.py` (+~4 tests). Existing tests unchanged. Total ~22 new tests. | All pass. |

## Smoke playbook updates

Append a Phase 6 section to `tests/manual/smoke_test.md`:
- Scenarios 1, 2, 3, 4 above (5–7 are pytest-covered; 4 needs debug logging which is manual).
- A pytest line confirming the full suite passes.

## Out of scope for Phase 6 (do NOT build)

- Skill *execution* beyond summarize (constrained-bash is Phase 11).
- Skill suggestion → auto-run without user invocation (Phase 8's Master Agent decides whether to apply a suggested skill; v0.1 Phase 6 just records the suggestion).
- Per-skill `model` override in frontmatter (use persona's model for v0.1).
- Skill-level governance (`require_approval` lives in Phase 14–15).
- Skill output going through the notification queue (Phase 7).
- Skill output writing to vault as a sidecar (`vault/summaries/` is post-v0.1).
- Sub-skills / skill composition.
- Skill discovery from user-writable dirs (only `config/skills/` for v0.1).

## Open questions to confirm before I start

1. **SKILL.md frontmatter required fields — `name`, `description`, `risk`, `reversibility` (recommended)?** Optional: `default_persona`, `prompts`. Unknown keys ignored. This is forward-compatible with Phase 11 (constrained-bash will add nothing new) and Phase 14 (governance just reads what we already store). OK?
2. **Skill resolution precedence — `pending_skill` > classifier suggestion > none?** Direct slash shortcuts like `/summary` bypass resolution entirely. I lean toward this order because explicit user intent (typed a slash) should beat classifier guess. Alternative: classifier suggestion wins so auto mode "just works". OK with explicit-wins?
3. **`/skill <name>` is one-shot, not sticky?** One-shot means it applies to the next text turn then clears. Sticky means it persists until `/skill clear`. One-shot is safer (no surprise state); sticky is more useful for "do all my next 5 messages as research". I lean one-shot for v0.1; sticky is trivial to add later.
4. **`/summary` does NOT persist anything (recommended)?** No `messages` row, no vault entry, no `after_send` event. The summary is a meta-command output, not part of the conversation. Alternative: write the summary as an assistant message with a `kind=summary` tag. I lean no-persist — keeps the conversation log clean and avoids "summary of summary" loops.
5. **`build_system_prompt` keeps current semantics (recommended option A from 6e)?** SKILL.md body becomes a "you have this skill" preamble; `prompts/<key>.md` is rendered into the user message. Alternative (B): `prompts/<key>.md` becomes the system prompt and SKILL.md body is discarded for activation. I lean (A) — persona stays in charge of voice.
6. **Classifier prompt injection — show full descriptions to the model?** With one skill in v0.1 this is one extra line. As skills grow, the description block grows. Cap at 10 skills × ~120 chars each = 1.2KB additional system prompt. Acceptable for v0.1; we can switch to embeddings-based retrieval in Phase 20.

If you don't push back, I'll go with the defaults above.

## Definition of done for Phase 6

- Five commits on `phase-6-skills` (6a, 6b, 6c, 6d-e, 6f).
- All 7 manual smoke scenarios pass (4 of them are scriptable; the rest are by-eye debug-log checks).
- New pytest suites for skills + REPL summary + classifier extensions; existing tests still pass.
- `tests/manual/smoke_test.md` Phase 6 section populated.
- `STATUS.md` Phase 6 row → Complete; LOC count updated; stale Phase 5 line in the Overall paragraph corrected.
- Branch handed to you for merge. Don't merge.
