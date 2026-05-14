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
| 1.5 | One-shot with `--persona` | `uv run python -m ubongo send "hello" --persona operator` | stdout: a real operator-voiced reply (terse, actionable). rc 0. (Pre-Phase-2 this was the literal echo `[operator] hello`; from Phase 2 onward oneshot routes through the LLM.) |
| 1.6 | One-shot default persona | `uv run python -m ubongo send "hi"` | stdout: a real architect-voiced reply. rc 0. (Pre-Phase-2: `[architect] hi`.) |
| 1.7 | Unknown slash command | In REPL: `/foo` | `Unknown command: /foo. Try /architect, /operator, /casual, /auto, /skill <name>, /skills, /summary, /queue, /decisions, /agents, /trace, /exec <cmd>, /mode <workflow>, /reload, /exit.` Loop continues. (Help line grows as later phases add commands.) |
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
| 4.3 | Compaction trigger | Drive 16+ turns into one conversation (a small bash loop with `ubongo send` works). | `sqlite3 data/ubongo.db "SELECT id, covers_from_message_id, covers_to_message_id, strategy FROM summaries"` shows at least one row with `strategy='default'` and `covers_to_message_id ≤ max(messages.id) - recall_turns`; `covers_to_message_id` advances on each subsequent fold. stderr has a `compaction_run` log line on each triggering turn. Compaction fires on `after_recall` (the user-turn boundary), so fold cadence depends on `trigger_at_turns` (15 by default) and not on every individual message; do not expect `covers_to = max - recall_turns` exactly on the latest fold. |
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
| 5.3 | Handler disable | `uv run python -c "from ubongo import events; from ubongo.agents.memory import default_memory_agent; events.unregister('after_send', default_memory_agent.project_vault); from ubongo import oneshot; oneshot.run('test', 'casual')"`; check that today's vault file size did not grow but `messages` count did | Vault file unchanged; `sqlite3 data/ubongo.db "SELECT COUNT(*) FROM messages"` increased by 2. (Phase 9 moved the `after_send` registration off `vault._after_send_handler` and onto `MemoryAgent.project_vault` to enforce the single-writer rule.) |
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
| 6.4 | Body lazy-load | `uv run python -c "from ubongo.logging import setup_logging; setup_logging(); from ubongo import skills; skills.list_skills()" 2>&1 \| grep skill_body_loaded` ; then `uv run python -c "from ubongo.logging import setup_logging; setup_logging(); from ubongo import skills; skills.list_skills(); skills.body('summarize-conversation')" 2>&1 \| grep skill_body_loaded` | First command emits nothing (no `skill_body_loaded` at discovery). Second command emits one `skill_body_loaded` JSON line. (The `setup_logging()` call is required because `python -c` doesn't go through `__main__` where logging is configured.) |
| 6.5 | Classifier suggests skill | REPL: `/auto`, then `can you wrap this up for me` | stderr `classify` log line includes `suggested_skill=summarize-conversation`. The reply runs through the normal turn flow with the skill body in the system prompt (no `/summary` shortcut needed). |
| 6.6 | `/skill <name>` is one-shot | REPL: `/skill summarize-conversation`; then send `hi`; then send `hi again` | After `/skill ...`: `Next turn will use skill: summarize-conversation.` The `hi` turn includes the skill body in the system prompt; the `hi again` turn does not. |
| 6.7 | Unknown skill rejected | REPL: `/skill phantom` | `Unknown skill: phantom.` REPL state unchanged; next text turn runs normally without any skill. |
| 6.8 | Pytest passes | `uv run pytest tests/` | All tests pass (109 expected after Phase 6). |

## Phase 7 — Minimal Outbound Queue

Every LLM-generated response writes a row to `notification_queue` before stdout. `handle_text` enqueues (`source='response'` on success, `'error'` on LLM failure), dequeues, fires `before_send`, returns a delivery token. The caller prints, then `delivery.queue.flush_delivered(token)` fires `after_send` and marks the row delivered. Slash echoes and `/summary` stay direct-print (out of scope per Phase 7 plan). On queue round-trip failure, output is still printed but no events fire and vault is skipped.

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 7.1 | Queue contains the response | `rm -f data/ubongo.db`; `uv run python -m ubongo send "hello" --persona casual`; `sqlite3 data/ubongo.db "SELECT id, urgency, source, delivered_at IS NOT NULL AS delivered FROM notification_queue"` | one row, `urgency='urgent'`, `source='response'`, `delivered=1`. |
| 7.2 | `/queue` table | send 3 messages with `ubongo send`; then REPL `/queue` | header `Recent queue (last 10):` followed by 3 rows, newest first; each row has id, two `HH:MM:SS` timestamps, `urgent`, `response`, and a content preview. |
| 7.3 | `/queue N` argument | After 7.2: REPL `/queue 1` | exactly one row (most recent). `/queue abc` prints `Usage: /queue [N]. …` |
| 7.4 | Latency | `time uv run python -m ubongo send "hi" --persona casual` | indistinguishable from pre-Phase-7 (queue insert is microseconds; the LLM call dominates). |
| 7.5 | `before_send` hook fires before stdout | `uv run python -c "from ubongo import events; events.register('before_send', lambda p: print('GOT', p['row_id'], file=__import__('sys').stderr)); from ubongo import oneshot; oneshot.run('hi', 'casual')"` | response on stdout; `GOT <id>` on stderr; vault entry present. |
| 7.6 | Vault still works on happy path | `rm -f vault/daily/$(date -u +%Y-%m-%d).md`; `ubongo send "vault check" --persona casual` | vault file recreated with the turn (vault writes only on delivery success, but happy path delivers). |
| 7.7 | Error path enqueues with source='error', skips vault | `rm -f data/ubongo.db vault/daily/$(date -u +%Y-%m-%d).md`; `OPENROUTER_API_KEY=sk-or-v1-bogus uv run python -m ubongo send "hi" --persona casual`; `sqlite3 data/ubongo.db "SELECT source, delivered_at IS NOT NULL FROM notification_queue"` | stdout: `Sorry, I couldn't reach the model. Check the logs.`; rc 1; queue row with `source='error'`, delivered=1; no vault file for today. |
| 7.8 | Pytest passes | `uv run pytest tests/` | all green (142 expected after Phase 7: prior 110 + 12 queue API + 10 send path + 10 /queue rendering). |

## Phase 8 — Master Agent

`MasterAgent.handle(message, persona, auto_mode, pending_skill)` is the single orchestration seam for every turn: `classify → plan → execute → decide → compose → enqueue`. REPL and oneshot delegate to it. Every turn writes a `workflow_runs` row and a `governance_decisions` row (action=`auto`; real matrix in Phase 14) and emits a `master_decision` log line. `/decisions [N]` renders the recent decisions table.

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 8.1 | Behavior parity | `rm -f data/ubongo.db`; `ubongo send "design a circuit breaker" --persona architect`; then `ubongo send "ugh long day" --persona casual` | Same shape of response as Phase 7 baseline; persona voice unchanged; queue still populated; vault still written. |
| 8.2 | `master_decision` log | After 8.1: `ubongo send "design a circuit breaker" --persona architect 2>/tmp/p8.err`; `grep master_decision /tmp/p8.err` | one JSON line with `intent`, `persona="architect"`, `execution_mode="sequential"`, `risk` set, `action="auto"`, `workflow_run_id` and `decision_id` populated. |
| 8.3 | `workflow_runs` + `governance_decisions` populated | After 8.2: `sqlite3 data/ubongo.db "SELECT id, execution_mode, outcome FROM workflow_runs"`; `sqlite3 data/ubongo.db "SELECT workflow_run_id, action FROM governance_decisions"` | each table has rows; `execution_mode='sequential'`, `outcome='success'`, `action='auto'`, FK matches. |
| 8.4 | `/decisions` table | Send 3 messages; REPL `/decisions` | header `Recent decisions (last 10):`, 3 rows newest-first with id, decided_at, intent, persona, mode, risk, conf, action. |
| 8.5 | `/decisions N` | `/decisions 1` after 8.4 | exactly one row (most recent). `/decisions abc` → `Usage: /decisions [N]. …`. |
| 8.6 | High-risk passthrough | Send a destructive-looking prompt (e.g. `ubongo send "rm -rf /" --persona operator`); inspect `master_decision` log | `risk` set by classifier; `action=auto` (Phase 14 will tighten this). |
| 8.7 | Classifier crash | `OPENROUTER_API_KEY=sk-or-v1-bogus ubongo send "hi" --persona casual` | stdout: polite error; rc 1; `master_decision` line with `confidence=0.0`, `action=auto`; `workflow_runs.outcome='failure'`; `governance_decisions` row exists. |
| 8.8 | Pytest passes | `uv run pytest tests/` | all green (181 expected after Phase 8: prior 142 + 5 governance + 19 master + 4 master persistence + 3 store + 8 /decisions). |

## Phase 9 — First Workers (Research + Memory)

Real worker agents enter the system. `MasterAgent.execute` delegates to `WorkflowRunner.execute(workflow, ctx, message, workflow_run_id)`, which dispatches the agents listed in `workflow.agents` sequentially, threading prior agents' output text via `AgentInput.prior_findings` and writing one `agent_runs` row per dispatch. `workflows.yaml` declares the per-workflow agent list and mode; `research_brief` runs `["research", "persona:architect"]`. Memory Agent owns the assistant-message write and the vault `after_send` projection (single-writer rule, soft enforcement in production / strict-mode pytest fixture). `workflow_runs` rows now persist with `outcome='in_progress'` before execute, then UPDATE to success/failure. `/agents` lists the registered workers.

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 9.1 | Behavior parity for non-research | `rm -f data/ubongo.db`; `ubongo send "design a circuit breaker" --persona architect`; `ubongo send "ugh long day" --persona casual` | Same shape responses as Phase 8 baseline; queue populated; vault note written. `sqlite3 data/ubongo.db "SELECT json_extract(workflow,'$.agents') FROM workflow_runs ORDER BY id"` → `["persona:architect"]` then `["persona:casual"]`. |
| 9.2 | Research dispatched | `ubongo send "we should add a caching layer" --persona architect` (seed); then in REPL: `/auto` followed by `research what we discussed about caching` (auto-mode required because oneshot CLI has no `--auto` flag) | Second response uses retrieved findings; `sqlite3 data/ubongo.db "SELECT json_extract(workflow,'$.agents') FROM workflow_runs ORDER BY id DESC LIMIT 1"` → `["research","persona:architect"]`. |
| 9.3 | `/agents` | REPL `/agents` | Header `Registered agents:` and rows for `architect`, `casual`, `coding`, `critic`, `evaluator`, `execution`, `memory`, `operator`, `repair`, `research` with role + model. (Bare persona names + Evaluator/Critic land in Phase 10; Coding/Execution/Repair land in Phase 11. Pre-Phase-10 the persona names were `persona:<x>` and the table excluded the Phase 10/11 workers.) |
| 9.4 | `agent_runs` populated | After 9.2: `sqlite3 data/ubongo.db "SELECT agent, outcome FROM agent_runs WHERE workflow_run_id=(SELECT MAX(id) FROM workflow_runs) ORDER BY id"` | three rows for the research turn: `research`, `persona:architect`, `memory`, all `outcome='success'`. |
| 9.5 | Memory single-writer test | `uv run pytest tests/test_agents_memory.py::test_strict_mode_blocks_non_memory_writer` | passes — synthetic non-Memory caller into `store.append_message(role='assistant')` raises under the fixture. |
| 9.6 | Casual still works (regression) | `/casual`; `long day` in REPL | Single-agent persona response; casual voice; `sqlite3 data/ubongo.db "SELECT agent FROM agent_runs WHERE workflow_run_id=(SELECT MAX(id) FROM workflow_runs)"` → two rows: `persona:casual` then `memory`. |
| 9.7 | Research LLM failure | Send a research-style message with the research model temporarily mocked to fail (e.g. patch in a REPL session) | Response is the persona answering without findings (no crash); `sqlite3 data/ubongo.db "SELECT agent, outcome FROM agent_runs WHERE workflow_run_id=(SELECT MAX(id) FROM workflow_runs)"` shows `research` outcome=`failure` and `persona:architect` outcome=`success`; `workflow_runs.outcome='success'`. |
| 9.8 | `workflow_runs` lifecycle | `sqlite3 data/ubongo.db "SELECT outcome FROM workflow_runs ORDER BY id DESC LIMIT 1"` after any turn | `success` (or `failure`); never `in_progress` after the turn completes. Mid-turn (during the LLM call) the row is `in_progress`; this is observable if you kill the process mid-turn. |
| 9.9 | `master_decision` still emitted | `ubongo send "research caching" --persona architect 2>/tmp/p9.err`; `grep master_decision /tmp/p9.err` | one JSON line per turn with the Phase-8 fields plus `agents` listing the workflow's agent tuple. |
| 9.10 | Pytest passes | `uv run pytest tests/` | all green (216 expected after Phase 9: Phase-8's 181 + 6 base + 8 research + 8 memory + 8 runner + 5 /agents). |

## Phase 10 — Evaluator + Critic + Persona Agents

Three new workers and one formalization. `EvaluatorAgent` is LLM-as-judge over the persona's response; it returns `{confidence: float, issues: [str]}` and the runner harvests its score onto `WorkflowResult.evaluator_confidence`. `CriticAgent` provides a contrarian frame; it is invoked by Master in one path: when evaluator confidence falls in `[0.2, 0.6)`, Master runs a second pass `(critic, persona)` under the same `workflow_run_id` and the retry's text becomes the response. Persona Agents are now class-based (`ArchitectPersona` / `OperatorPersona` / `CasualPersona` subclassing `BasePersonaAgent`) and live in the registry under bare names (`architect`, not `persona:architect`). `workflows.yaml` gains a per-workflow `evaluate: true|false` flag; when true Master appends `evaluator` to `workflow.agents` at plan time. `governance.decide` rejects when evaluator confidence is below 0.2 (`reason="evaluator_confidence_below_floor:X.XX"`); everything else still returns `auto`. `governance_decisions.confidence` now stores the evaluator's score when present. `/trace [n]` prints recent workflow_runs with classification, workflow.agents, agent rows (timings + tokens + confidence), and the governance decision.

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 10.1 | Evaluator runs on technical workflow | `rm -f data/ubongo.db`; in REPL: `/architect`; type `What's a sensible retry strategy for OpenRouter calls?` | Response composed by architect. `sqlite3 data/ubongo.db "SELECT agent FROM agent_runs WHERE workflow_run_id=(SELECT MAX(id) FROM workflow_runs) ORDER BY id"` → `architect`, `evaluator`, `memory`. `sqlite3 data/ubongo.db "SELECT json_extract(workflow,'$.agents') FROM workflow_runs ORDER BY id DESC LIMIT 1"` → `["architect","evaluator"]`. The evaluator row's `confidence` is a float in `[0,1]`. |
| 10.2 | Casual workflow skips evaluator | After 10.1: `/casual`; type `long day` | `sqlite3 data/ubongo.db "SELECT agent FROM agent_runs WHERE workflow_run_id=(SELECT MAX(id) FROM workflow_runs) ORDER BY id"` → `casual`, `memory`. No `evaluator` row; no `evaluate=true` in this workflow. |
| 10.3 | Persona Agent class rename | After 10.1: `sqlite3 data/ubongo.db "SELECT DISTINCT agent FROM agent_runs ORDER BY agent"` | Includes `architect` (bare), not `persona:architect`. Also includes `evaluator` and `memory`. |
| 10.4 | `/agents` updated table | REPL `/agents` | Header `Registered agents:` with seven rows in the Phase-10 form: `architect`, `casual`, `critic`, `evaluator`, `memory`, `operator`, `research`. Each has role + model (memory shows `—` for model). Phase 11 expands this to ten rows; see scenario 11.8. |
| 10.5 | `/trace 1` shows the latest turn | After 10.1: REPL `/trace 1` | One block beginning `--- workflow_run #N (conv …, msg …) ---`. Shows `classification: intent=technical …`, `workflow: persona=architect mode=sequential agents=[architect,evaluator]`, agent lines for `architect` and `evaluator` and `memory` in order with timings, evaluator row showing `conf=0.??`, governance line `action=auto`. |
| 10.6 | Borderline → Critic re-dispatch (test harness) | `uv run pytest tests/test_master.py::test_borderline_confidence_invokes_critic` | Pass. Mock evaluator returns 0.45; Master executes a second pass with `agents=("critic", "architect")` under the same `workflow_run_id`; response text becomes the retry's text. |
| 10.7 | Reject on very low confidence (test harness) | `uv run pytest tests/test_master.py::test_low_confidence_rejects` | Pass. Mock evaluator returns 0.1; `governance_decisions.action='reject'`; response text is the canned `_REJECT_MESSAGE`; `governance_decisions.confidence` matches the evaluator score (0.1). |
| 10.8 | Governance confidence uses evaluator score | After 10.1: `sqlite3 data/ubongo.db "SELECT action, confidence FROM governance_decisions ORDER BY id DESC LIMIT 1"` | `action=auto`; `confidence` matches the most recent `evaluator` agent_run's `confidence` (not the classifier's). For 10.2 (no evaluator), `confidence` falls back to the classifier's score. |
| 10.9 | Regression: `research_brief` still works | In REPL: `/auto`; type `research what we discussed about caching` after seeding a few caching messages | `sqlite3 data/ubongo.db "SELECT agent FROM agent_runs WHERE workflow_run_id=(SELECT MAX(id) FROM workflow_runs) ORDER BY id"` → `research`, `architect`, `evaluator`, `memory`. |
| 10.10 | Help line includes `/trace` | REPL: `/foo` (any unknown slash) | Help banner lists `/trace` between `/agents` and `/reload`. |
| 10.11 | Pytest passes | `uv run pytest tests/` | All green (~273 expected after Phase 10: Phase-9's 216 + 9 evaluator + 5 critic + 6 persona-classes + 9 /trace + 5 router + 6 master Phase-10 + 4 governance Phase-10 - some replaced). |

## Phase 11 — Coding + Execution + Repair Agents

Three new workers and the `coding_session` workflow go live. **Coding Agent** (`composer=True`, `models.coding`) produces the function with type hints + a usage example + named assumptions. **Sandbox** (`src/ubongo/sandbox.py`) is the entire safety contract for shell execution: 18-command allowlist, no shell metacharacters anywhere, no path traversal, `shell=False`, restricted PATH, repo-root cwd, 10s default timeout. **Execution Agent** (`composer=False`) bridges from a target command to `sandbox.run_constrained` and formats stdout/stderr/exit-code. **Repair Agent** (`composer=False`) lives in the registry but never runs as a workflow step; the WorkflowRunner consults `RepairAgent.plan_retry` on `agent_failed` and re-dispatches the failing agent ONCE with a model fallback. `agent_runs.retried` boolean column added (with migration shim). The `/exec <cmd>` REPL command bypasses `master.handle` entirely (debug-only). `coding_session` workflow is `("coding", "architect")` — last-composer-wins makes the architect's wrap the final response. `execution_session` is declared in `workflows.yaml` but NOT auto-routed (Phase 15 approval gate first).

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 11.1 | Coding Agent produces a function | `rm -f data/ubongo.db`; in REPL: `/auto`; type `write a Python function that reverses a list` | Response is a `def reverse_list(lst: list[T]) -> list[T]:` style function with type hints + a docstring + a usage example. `sqlite3 data/ubongo.db "SELECT json_extract(workflow,'$.agents') FROM workflow_runs ORDER BY id DESC LIMIT 1"` → `["coding","architect","evaluator"]`. `agent_runs` rows in order: `coding`, `architect`, `evaluator`, `memory`. |
| 11.2 | `/exec` happy path | REPL: `/exec echo hello world` | Block beginning `$ echo hello world`, line `exit=0  (<n>ms)`, then `stdout:\nhello world` and `stderr:` (empty). |
| 11.3 | `/exec` refused — disallowed program | REPL: `/exec rm -rf /` | `Refused: program 'rm' not in allowlist`. No filesystem mutation. |
| 11.4 | `/exec` refused — shell metachar | REPL: `/exec ls; cat /etc/passwd` | `Refused: shell metacharacter ';' rejected`. |
| 11.5 | `/exec` refused — path traversal | REPL: `/exec cat ../../etc/passwd` | `Refused: path fragment '..' rejected in argument '../../etc/passwd'`. |
| 11.6 | Repair single-retry succeeds | `uv run pytest tests/test_runner.py::test_repair_retries_failing_agent_once` | Pass. Two `agent_runs` rows for the failing agent; second has `retried=1`; final `WorkflowResult.ok=True`. |
| 11.7 | Repair gives up after retry also fails | `uv run pytest tests/test_runner.py::test_repair_gives_up_after_second_failure` | Pass. Two rows, both `outcome='failure'`, `WorkflowResult.ok=False`. |
| 11.8 | `/agents` includes new workers | REPL: `/agents` | Ten rows: `architect`, `casual`, `coding`, `critic`, `evaluator`, `execution`, `memory`, `operator`, `repair`, `research`. Each has role + model (memory + execution + repair show `—` for model). |
| 11.9 | `/exec` does NOT create a workflow_run | After 11.2: `sqlite3 data/ubongo.db "SELECT COUNT(*) FROM workflow_runs WHERE workflow LIKE '%exec%'"` | 0. `/exec` is debug-only. |
| 11.10 | Help line includes `/exec` | REPL: `/foo` | Help banner lists `/exec <cmd>` between `/trace` and `/reload`. |
| 11.11 | `/trace` shows retry suffix on retried rows | After 11.6 inside a unit test or after a forced REPL failure (test harness only): `/trace 1` | The retried agent_run row ends with `(retried)` after the conf= column. |
| 11.12 | execution_session declared but not auto-routed | `uv run pytest tests/test_router.py::test_execution_session_declared_but_not_auto_routed` | Pass. The workflow exists (`workflow_agents("execution_session") == ("execution","architect")`) but no classification routes to it. |
| 11.13 | Pytest passes | `uv run pytest tests/` | All green (~326 expected after Phase 11: Phase-10's 273 + 6 coding + 15 sandbox + 7 execution + 9 repair + 4 runner-retry + 8 /exec + 2 router + 1 skill + 1 store - replacements). |

## Phase 12 — Execution Modes (all six)

End of Tier 2 (Multi-Agent System). The WorkflowRunner now supports all six execution modes. Internally it is async (each mode is a strategy coroutine selected off `workflow.execution_mode`) but its public `execute()` stays sync via `asyncio.run`, so `master.handle` and the REPL stay sync. Sequential agents run serially with Phase 11 Repair retry; parallel/competitive/collaborative use `asyncio.gather` (no Repair in fan-out modes — cancel-and-retry semantics are ambiguous; Phase 13 may revisit). `EvaluatorAgent` gained `rank()` (competitive winner picker, returns `{winner, winner_index, reason, scores}`) and `agree()` (speculative agreement check; bool/None). `Workflow` dataclass gained optional `rounds` (debate; default 2) and `timeout_s` (speculative; default 10). PersonaAgents read `debate_role` from `input.metadata` (`"challenge"` for round-2+ debaters, `"synthesize"` for the synthesizer). `/mode <workflow>` REPL command pins a workflow for the next turn (mirrors `/skill`'s one-shot pattern); `/mode list` prints every declared workflow with its mode. `master.plan` skips the Phase-10 evaluator-append for competitive (the trailing evaluator is part of the mode contract).

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 12.1 | Sequential regression | Any technical question via `/architect` | Same as Phase 10 baseline; agent_runs in order; `mode=sequential`. |
| 12.2 | Parallel via `/mode` | `rm -f data/ubongo.db`; in REPL: `/mode research_brief_parallel`; `compare Postgres vs DynamoDB for an event store` | Response composed by architect; `agent_runs` shows `research` + `architect` with overlapping `started_at` / `ended_at` (parallel); `sqlite3 data/ubongo.db "SELECT json_extract(workflow,'$.execution_mode') FROM workflow_runs ORDER BY id DESC LIMIT 1"` → `"parallel"`. Total wall time near max(research, architect), not sum. |
| 12.3 | Competitive via `/mode` | `/mode coding_competitive`; `write a Python function that reverses a list` | Two competitor `agent_runs` rows (`coding`, `architect`); one `evaluator` row with `confidence` populated and `output.winner` set to the winning agent's name; `WorkflowResult.text` matches the winner's text. |
| 12.4 | Collaborative via `/mode` | `/mode brief_collaborative`; `give me a brief on adopting microservices` | Response is a structured document with `## retrieval and synthesis ...`, `## contrarian challenger ...`, `## persona composer` headings (under each agent's role). `agent_runs` shows research, critic, architect (parallel), then evaluator (sequential after merge). |
| 12.5 | Debate via `/mode` | `/mode debate_then_synthesize`; `should we use microservices for a 5-engineer team` | 5 agent_runs rows: architect, operator, architect, operator, architect (synthesizer). Synthesizer's text is the response; reads as a synthesis (recommendation + residual risk). |
| 12.6 | Speculative agree | `/mode speculative_brief`; `what is the capital of France` | Response is the cheap (casual) reply; no `[Correction]` block. agent_runs shows casual, architect, evaluator (agreement check, `output.agree=true`). Total wall time bounded by `timeout_s` (10s). |
| 12.7 | Speculative disagree | `/mode speculative_brief`; ask a deliberately ambiguous question where casual and architect would diverge | Response begins with cheap text, then `---`, then `[Correction (slower model):]` block with strong's text. evaluator row has `output.agree=false`. |
| 12.8 | `/mode list` | `/mode list` | Lists all workflows from `workflows.yaml` with `mode=` and `agents=[...]` columns. Includes the 5 Phase-12 workflows. |
| 12.9 | `/mode unknown` | `/mode phantom` | `Unknown workflow: phantom.` REPL state unchanged. |
| 12.10 | `/mode` is one-shot | `/mode brief_collaborative`, then any turn, then another turn | Second turn does NOT use brief_collaborative (back to routed default). |
| 12.11 | Unknown mode in workflows.yaml falls back | `uv run pytest tests/test_router.py::test_unknown_mode_in_workflows_yaml_falls_back_to_sequential` | Pass. router.workflow_mode logs a warning and returns `sequential` for unknown declared modes. |
| 12.12 | Help line includes `/mode` | `/foo` | Help banner lists `/mode <workflow>`. |
| 12.13 | Pytest passes | `uv run pytest tests/` | All green (~381 expected after Phase 12: Phase-11's 326 + 8 parallel + 13 evaluator (rank+agree+parsers) + 5 competitive + 4 collaborative + 4 debate + 7 speculative + 6 router + 6 /mode). |

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
