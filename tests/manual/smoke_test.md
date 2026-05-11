# Ubongo — Manual Smoke Test Playbook

This playbook is the cumulative end-to-end manual test for Ubongo. After every implementation phase is complete, walk through every section that exists. New phases append a new section.

The point: at every phase boundary, the *entire* Ubongo system should still work. New features land additively; old features must not regress.

Each section's scenarios are run in order. If a scenario fails, the phase is not complete.

## How to use

1. Make sure you're on `main` with the latest merge.
2. Set up: `uv sync`; `cp .env.example .env`; set `OPENROUTER_API_KEY`.
3. From section "Phase 0" forward, work through every section that's present.
4. Mark each scenario pass/fail in your local copy. Do not commit pass/fail marks; they're per-run.

## Phase 0 — Skeleton

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 0.1 | Cold start | `uv run python -m ubongo` (with `OPENROUTER_API_KEY` set in `.env`); pipe `/exit` to stdin so the REPL exits | One JSON line on stderr with `event="startup"`, `level="INFO"`, ISO8601 `ts`, redacted config summary. rc 0. |
| 0.2 | Missing API key | Move `.env` aside (`mv .env .env.bak`); `unset OPENROUTER_API_KEY`; `uv run python -m ubongo`; restore `.env` | rc 1; stderr: `Error: OPENROUTER_API_KEY not set. Copy .env.example to .env and fill it in.`; no traceback. |
| 0.3 | Hierarchical context assembly | `uv run python -c "from ubongo.context import build_system_prompt; print(build_system_prompt('architect'))"` | Output begins with `# UBONGO.md` body, blank line, then architect persona body (frontmatter stripped). |
| 0.4 | Log structure | `uv run python -m ubongo 2>&1 1>/dev/null \| jq .` (pipe `/exit` to stdin) | Valid JSON; has `event`, `level`, `ts`. `OPENROUTER_API_KEY` value is not present in the output. |

## Phase 1 — CLI REPL + One-Shot (echo)

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1.1 | REPL banner + default response | `uv run python -m ubongo`; type `hello` | First line: `Ubongo REPL ready. /exit to quit.` Then a `>` prompt. Substantive architect-voiced response (Phase 2 onward; pre-Phase-2 was bracket echo). |
| 1.2 | Persona switch | After 1.1: `/casual`, then `hello` | `Switched to casual.` then a warm casual-voiced response. |
| 1.3 | `/auto` enables auto routing | After 1.2: `/auto`, then `hello` | `Auto routing enabled.` then an LLM-voiced response. From Phase 3 onward the classifier picks the persona; before Phase 3 this fell back to the default architect. |
| 1.4 | `/exit` clean quit | type `/exit` | `Goodbye.` rc 0. |
| 1.5 | One-shot with `--persona` | `uv run python -m ubongo send "hello" --persona operator` | stdout: `[operator] hello`. rc 0. |
| 1.6 | One-shot default persona | `uv run python -m ubongo send "hi"` | stdout: `[architect] hi`. rc 0. |
| 1.7 | Unknown slash command | In REPL: `/foo` | `Unknown command: /foo. Try /architect, /operator, /casual, /auto, /exit.` Loop continues. |
| 1.8 | One-shot bad persona | `uv run python -m ubongo send "x" --persona bogus` | stderr: `Error: unknown persona 'bogus'. Choose from: architect, operator, casual.` rc 1. |
| 1.9 | EOF (Ctrl+D) | In REPL: press Ctrl+D | `Goodbye.` rc 0. |
| 1.10 | Pytest passes | `uv run pytest tests/test_repl.py` | All tests pass. |

## Phase 2 — LLM Integration

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 2.1 | Architect voice (depth) | `uv run python -m ubongo send "design a circuit breaker for an API gateway" --persona architect` | Substantive technical response: states, thresholds, tradeoffs. Markdown structure (sections, code blocks). Names what isn't decided yet. |
| 2.2 | Casual voice (warmth + brevity) | `uv run python -m ubongo send "ugh today sucked" --persona casual` | Short, present, human reply. Often a single sentence or a follow-up question. No advice unless asked. |
| 2.3 | Operator voice (terse + actionable) | `uv run python -m ubongo send "summarize my last 3 commits" --persona operator` | Terse. Honestly says it doesn't have git access; lists what would unblock it. No padding. |
| 2.4 | UBONGO.md propagates | Edit `config/UBONGO.md` to add a quirky directive (e.g. `Always begin every reply with "Right.,"`); restart; ask any question | Response respects the new directive. Restore `UBONGO.md` afterward. |
| 2.5 | Terminal LLM error | `OPENROUTER_API_KEY=sk-or-v1-bogus uv run python -m ubongo send "hi" --persona casual` | stdout: `Sorry, I couldn't reach the model. Check the logs.` rc 1. stderr has structured `llm_attempt_failed` (twice — single retry) and `llm_error` JSON lines. No traceback to stdout. |
| 2.6 | Pytest passes | `uv run pytest tests/` | All tests pass (18 expected after Phase 2: repl/personas/events). |

## Phase 3 — Tone Classifier + Auto Routing

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 3.1 | Auto-route to architect | REPL: `/auto`, `help me design a circuit breaker` | Substantive architect-voiced response. stderr `classify` log shows `intent=technical` (or `coding`), `confidence>=0.7`, `used=architect`. |
| 3.2 | Auto-route to casual | REPL: `/auto`, `ugh long day` | Short, warm casual reply. `classify` log shows `intent=casual` or `tone=tired`/`frustrated`, `used=casual`. |
| 3.3 | Hysteresis keeps architect on weak switch | REPL: `/auto`, send 3-5 technical questions, then `lol` | After `lol`, persona stays architect. `classify` log on the `lol` turn may show `suggested=casual` but `used=architect` because confidence falls below the 0.7 threshold or persona was already set by prior turns. |
| 3.4 | Manual override beats auto | REPL: `/auto`, technical question (auto picks architect), `/casual`, `something simple` | After `/casual`: `Switched to casual.` Next turn uses casual; no `classify` event logged for that turn (auto_mode is off). |
| 3.5 | Classifier failure (sanity) | Set `OPENROUTER_API_KEY=sk-or-v1-bogus`; REPL: `/auto`, `hi` | Classifier returns `_FALLBACK` (confidence 0.0); router falls back to default workflow (`casual`); stderr has `classify_failed` log. The conversation continues (LLM call also fails, so polite stdout error). The pytest suite is the gate for this path; this manual scenario is just a sanity check. |
| 3.6 | Pytest passes | `uv run pytest tests/` | All tests pass (41 expected after Phase 3: events/personas/repl/classifier/router). |

## Phase 4 — SQLite Memory + Compaction

DB lives at `./data/ubongo.db` (gitignored). To start with a clean state, `rm -f data/ubongo.db` before any of these. To advance the simulated clock for the timeout test, set `UBONGO_FAKE_NOW=<ISO8601>` in the environment.

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 4.1 | Persistence across restart | Process A: `uv run python -m ubongo send "I'm working on a project called Ubongo. Remember that." --persona casual`. Process B: `uv run python -m ubongo send "What was the project name I just told you?" --persona casual` | Process B response names `Ubongo`. `sqlite3 data/ubongo.db "SELECT COUNT(*) FROM messages"` returns 4. Both processes wrote into conversation id 1. |
| 4.2 | New session after timeout | Process A: `UBONGO_FAKE_NOW=2030-01-01T12:00:00+00:00 uv run python -m ubongo send "first turn"`. Process B: `UBONGO_FAKE_NOW=2030-01-01T12:31:00+00:00 uv run python -m ubongo send "second turn"` | `sqlite3 data/ubongo.db "SELECT id, started_at, ended_at FROM conversations"` shows 2 rows; conversation 1 has `ended_at` set to the original `last_message_at`. |
| 4.3 | Compaction trigger | Drive 16+ turns into one conversation (a small bash loop with `ubongo send` works). | `sqlite3 data/ubongo.db "SELECT id, covers_from_message_id, covers_to_message_id, strategy FROM summaries"` shows at least one row with `covers_to_message_id` = (max - 10). stderr has a `compaction_run` log line on the triggering turn. |
| 4.4 | Cumulative summary preserves early facts | Turn 1: `"My birthday is March 15. Remember that."` then 15+ filler turns ("just say ok"). Final turn: `"What's my birthday?"` | Final reply names March 15 (or equivalent). `sqlite3 data/ubongo.db "SELECT content FROM summaries ORDER BY id DESC LIMIT 1"` shows the latest summary contains "March 15". This is the bug-fix scenario for cumulative summaries. |
| 4.5 | Cross-session inheritance | After 4.4: set `UBONGO_FAKE_NOW` to a timestamp 31+ min after the last turn (e.g. `python3 -c "import datetime; print((datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=31)).isoformat())"`); send `"Do you know my birthday?"` | A new `conversations` row is created. The reply still names March 15 (it inherited the previous conversation's latest summary). |
| 4.6 | Compaction idempotency | After 4.3: add a few more turns without crossing the next 15-message trigger | New summaries may form, but each preserves the early facts (cumulative). `compaction_run` appears only on triggering turns. |
| 4.7 | Swappable strategy (pytest gates this) | `uv run pytest tests/test_memory_compaction.py::test_register_and_get_custom_strategy tests/test_memory_compaction.py::test_maybe_compact_at_threshold_persists_summary tests/test_memory_compaction.py::test_cumulative_summary_folds_prior_into_new` | All three tests pass; cumulative-summary test asserts the second compaction's prior-summary input matches the first compaction's output. |
| 4.8 | Persona persistence across restart | Process A: `printf '/casual\n/exit\n' \| uv run python -m ubongo`. Process B: start REPL, type `hi` | The `hi` reply is in casual voice. `sqlite3 data/ubongo.db "SELECT active_persona, auto_mode FROM sessions"` returns `casual`/`0`. |
| 4.9 | Pytest passes | `uv run pytest tests/` | All tests pass (69 expected after Phase 4). |

## Phase 5 — Markdown Vault Projection

Daily notes land at `vault/daily/YYYY-MM-DD.md`. Vault is gitignored except `vault/.gitkeep`. Format: YAML frontmatter (date + tags), H1 date, one `## HH:MM:SS — persona [(auto)]` H2 per turn with verbatim `**You:**` / `**Ubongo:**` bodies.

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 5.1 | Daily note write | `rm -f data/ubongo.db vault/daily/$(date -u +%Y-%m-%d).md`; send 3 messages with `ubongo send` | `vault/daily/<today>.md` exists with frontmatter, H1 date, three `## HH:MM:SS — <persona>` entries containing the user/assistant pairs verbatim. |
| 5.2 | Obsidian render (manual) | Open `vault/` as an Obsidian vault | Frontmatter shown in the properties panel; H1/H2 hierarchy correct; `**You:**` / `**Ubongo:**` bold; line wraps cleanly; Properties tags include `ubongo`, `daily`. |
| 5.3 | Handler disable | `uv run python -c "from ubongo import events; from ubongo.memory import vault; events.unregister('after_send', vault._after_send_handler); from ubongo import oneshot; oneshot.run('test', 'casual')"`; check that today's vault file size did not grow but `messages` count did | Vault file unchanged; `sqlite3 data/ubongo.db "SELECT COUNT(*) FROM messages"` increased by 2. |
| 5.4 | Date rollover | `UBONGO_FAKE_NOW="2030-06-15T23:50:00+00:00" ubongo send "before midnight" --persona casual`; `UBONGO_FAKE_NOW="2030-06-16T00:10:00+00:00" ubongo send "after midnight" --persona casual` | Both `vault/daily/2030-06-15.md` and `vault/daily/2030-06-16.md` exist with the respective entries. |
| 5.5 | Auto-routed suffix | `printf '/auto\nhelp me design a circuit breaker\n/exit\n' \| ubongo` | The vault file's H2 entry for that turn ends with `(auto)` after the persona name — e.g., `## HH:MM:SS — architect (auto)`. |
| 5.6 | Pytest passes | `uv run pytest tests/` | All tests pass (75 expected after Phase 5). |

## Phase 6 — Skills + Progressive Disclosure

Skills live under `config/skills/<name>/`. v0.1 ships `summarize-conversation`. The registry parses frontmatter at startup; bodies and `prompts/*.md` files load lazily on first activation (log lines `skill_body_loaded` / `skill_prompt_loaded`). `/reload` clears all caches (UBONGO.md, personas, skills). `/summary` is a meta-command — it does not persist a message row, does not dispatch `after_send`, and does not touch the vault.

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 6.1 | `/skills` lists registered | `uv run python -m ubongo`; type `/skills` | Table headed `Registered skills:` with one row containing `summarize-conversation`, `risk=low`, `reversibility=reversible`, and the description. |
| 6.2 | `/summary` produces a recap | `rm -f data/ubongo.db`; in REPL, send 5 messages on a related topic; then `/summary` | A 3–5 sentence operator-voice recap printed to stdout. `sqlite3 data/ubongo.db "SELECT COUNT(*) FROM messages"` is unchanged from before `/summary`; `vault/daily/<today>.md` size unchanged; no new `after_send` payload. |
| 6.3 | `/reload` picks up edits | Edit `config/skills/summarize-conversation/prompts/summarize.md` (e.g., add `Write the recap in haiku form.`); in REPL: `/reload`; then `/summary` | First reply: `Reloaded UBONGO.md, personas, and skills.` Second reply (`/summary`) reflects the edit. Restore the file afterward. |
| 6.4 | Body lazy-load | `uv run python -c "from ubongo import skills; skills.list_skills(); print('discovery done')" 2>&1 \| grep skill_body_loaded` ; then `uv run python -c "from ubongo import skills; skills.list_skills(); skills.body('summarize-conversation')" 2>&1 \| grep skill_body_loaded` | First command emits nothing (no `skill_body_loaded` at discovery). Second command emits one `skill_body_loaded` JSON line. |
| 6.5 | Classifier suggests skill | REPL: `/auto`, then `can you wrap this up for me` | stderr `classify` log line includes `suggested_skill=summarize-conversation`. The reply runs through the normal turn flow with the skill body in the system prompt (no `/summary` shortcut needed). |
| 6.6 | `/skill <name>` is one-shot | REPL: `/skill summarize-conversation`; then send `hi`; then send `hi again` | After `/skill ...`: `Next turn will use skill: summarize-conversation.` The `hi` turn includes the skill body in the system prompt; the `hi again` turn does not. |
| 6.7 | Unknown skill rejected | REPL: `/skill phantom` | `Unknown skill: phantom.` REPL state unchanged; next text turn runs normally without any skill. |
| 6.8 | Pytest passes | `uv run pytest tests/` | All tests pass (109 expected after Phase 6). |

## Phase 7 — Minimal Outbound Queue

*(Populated when Phase 7 is implemented.)*

## Phase 8 — Master Agent

*(Populated when Phase 8 is implemented.)*

## Phase 9 — First Workers (Research + Memory)

*(Populated when Phase 9 is implemented.)*

## Phase 10 — Evaluator + Critic + Persona Agents

*(Populated when Phase 10 is implemented.)*

## Phase 11 — Coding + Execution + Repair Agents

*(Populated when Phase 11 is implemented.)*

## Phase 12 — Execution Modes (all six)

*(Populated when Phase 12 is implemented.)*

## Phase 13 — Repair Agent Activated

*(Populated when Phase 13 is implemented.)*

## Phase 14 — Risk + Confidence Scoring

*(Populated when Phase 14 is implemented.)*

## Phase 15 — Approval Gates + Sandboxing

*(Populated when Phase 15 is implemented.)*

## Phase 16 — Variant Generation

*(Populated when Phase 16 is implemented.)*

## Phase 17 — Sandboxed Evaluation + Fitness

*(Populated when Phase 17 is implemented.)*

## Phase 18 — GP Loop (autonomous)

*(Populated when Phase 18 is implemented.)*

## Phase 19 — GP Targets Expanded + Promotions

*(Populated when Phase 19 is implemented.)*

## Phase 20 — Embeddings + Graph

*(Populated when Phase 20 is implemented.)*

## Phase 21 — Bidirectional Vault Sync + Audit

*(Populated when Phase 21 is implemented.)*
