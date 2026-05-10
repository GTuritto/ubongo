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
| 4.3 | Compaction trigger | Drive 31 turns into one conversation (a small bash loop with `ubongo send` works). | `sqlite3 data/ubongo.db "SELECT id, covers_from_message_id, covers_to_message_id, strategy FROM summaries"` shows one row with `covers_to_message_id` = (max - 10). stderr has a `compaction_run` log line on the triggering turn. |
| 4.4 | Compaction idempotency | After 4.3: send 5 more turns | `sqlite3 data/ubongo.db "SELECT COUNT(*) FROM summaries"` is still 1. No new `compaction_run` log entry on the new turns. |
| 4.5 | Swappable strategy (pytest gates this) | `uv run pytest tests/test_memory_compaction.py::test_register_and_get_custom_strategy tests/test_memory_compaction.py::test_maybe_compact_at_threshold_persists_summary` | Both tests pass; the second uses a stub strategy registered as `stub` and the persisted summary's `content` is `STUB:21`. |
| 4.6 | Persona persistence across restart | Process A: `printf '/casual\n/exit\n' \| uv run python -m ubongo`. Process B: start REPL, type `hi` | The `hi` reply is in casual voice. `sqlite3 data/ubongo.db "SELECT active_persona, auto_mode FROM sessions"` returns `casual`/`0`. |
| 4.7 | Pytest passes | `uv run pytest tests/` | All tests pass (65 expected after Phase 4). |

## Phase 5 — Markdown Vault Projection

*(Populated when Phase 5 is implemented.)*

## Phase 6 — Skills + Progressive Disclosure

*(Populated when Phase 6 is implemented.)*

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
