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
| 11.8 | `/agents` includes new workers | REPL: `/agents` | Ten rows at Phase 11: `architect`, `casual`, `coding`, `critic`, `evaluator`, `execution`, `memory`, `operator`, `repair`, `research` (memory + execution + repair show `—` for model). **From v0.1.5 the table has eleven rows — `connector` joins (candidate 20).** |
| 11.9 | `/exec` does NOT create a workflow_run | After 11.2: `sqlite3 data/ubongo.db "SELECT COUNT(*) FROM workflow_runs WHERE workflow LIKE '%exec%'"` | 0. `/exec` is debug-only. |
| 11.10 | Help line includes `/exec` | REPL: `/foo` | Help banner lists `/exec <cmd>` between `/trace` and `/reload`. |
| 11.11 | `/trace` shows retry suffix on retried rows | After 11.6 inside a unit test or after a forced REPL failure (test harness only): `/trace 1` | The retried agent_run row ends with `(retried)` after the conf= column. |
| 11.12 | execution_session declared but not auto-routed | `uv run pytest tests/test_router.py::test_execution_session_declared_but_not_auto_routed` | Pass. The workflow exists (`workflow_agents("execution_session") == ("execution","architect")`) but no classification routes to it. |
| 11.13 | Pytest passes | `uv run pytest tests/` | All green (~326 expected after Phase 11: Phase-10's 273 + 6 coding + 15 sandbox + 7 execution + 9 repair + 4 runner-retry + 8 /exec + 2 router + 1 skill + 1 store - replacements). |

## Phase 12 — Execution Modes (all six)

End of Tier 2 (Multi-Agent System). The WorkflowRunner now supports all six execution modes. Internally it is async (each mode is a strategy coroutine selected off `workflow.execution_mode`) but its public `execute()` stays sync via `asyncio.run`, so `master.handle` and the REPL stay sync. Sequential agents run serially with Phase 11 Repair retry; parallel/competitive/collaborative use `asyncio.gather` (at this phase, no Repair in fan-out modes — cancel-and-retry semantics are ambiguous; Phase 13c later added single-hop peer replacement to all five fan-out modes). `EvaluatorAgent` gained `rank()` (competitive winner picker, returns `{winner, winner_index, reason, scores}`) and `agree()` (speculative agreement check; bool/None). `Workflow` dataclass gained optional `rounds` (debate; default 2) and `timeout_s` (speculative; default 10). PersonaAgents read `debate_role` from `input.metadata` (`"challenge"` for round-2+ debaters, `"synthesize"` for the synthesizer). `/mode <workflow>` REPL command pins a workflow for the next turn (mirrors `/skill`'s one-shot pattern); `/mode list` prints every declared workflow with its mode. `master.plan` skips the Phase-10 evaluator-append for competitive (the trailing evaluator is part of the mode contract).

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 12.1 | Sequential regression | Any technical question via `/architect` | Same as Phase 10 baseline; agent_runs in order; `mode=sequential`. |
| 12.2 | Parallel via `/mode` | `rm -f data/ubongo.db`; in REPL: `/mode research_brief_parallel`; `compare Postgres vs DynamoDB for an event store` | Response composed by architect; `agent_runs` shows `research` + `architect` with overlapping `started_at` / `ended_at` (parallel); `sqlite3 data/ubongo.db "SELECT json_extract(workflow,'$.execution_mode') FROM workflow_runs ORDER BY id DESC LIMIT 1"` → `"parallel"`. Total wall time near max(research, architect), not sum. |
| 12.3 | Competitive via `/mode` | `/mode coding_competitive`; `write a Python function that reverses a list` | Two competitor `agent_runs` rows (`coding`, `architect`); one `evaluator` row with `confidence` populated and `output.winner` set to the winning agent's name; `WorkflowResult.text` matches the winner's text. |
| 12.4 | Collaborative via `/mode` | `/mode brief_collaborative`; `give me a brief on adopting microservices` | Response is a structured document with `## retrieval and synthesis ...`, `## contrarian challenger ...`, `## persona composer` headings (under each agent's role). `agent_runs` shows research, critic, architect (parallel), then evaluator (sequential after merge). |
| 12.5 | Debate via `/mode` | `/mode debate_then_synthesize`; `should we use microservices for a 5-engineer team` | 5 agent_runs rows: architect, operator, architect, operator, architect (synthesizer). Synthesizer's text is the response; reads as a synthesis (recommendation + residual risk). |
| 12.6 | Speculative agree | `/mode speculative_brief`; `what is the capital of France` | Response is the cheap (casual) reply; no `[Correction]` block. agent_runs shows casual, architect, evaluator (agreement check, `output.agree=true`). Total wall time bounded by `timeout_s` (10s). |
| 12.7 | Speculative disagree (correction-block path) | `uv run pytest tests/test_runner.py::test_speculative_both_ok_disagree_appends_correction` | Pass. Mock evaluator returns `agree=false`; `WorkflowResult.text` begins with cheap's text, then `---`, then `[Correction (slower model):]` block with strong's text. **Manual REPL trigger is non-deterministic by design**: with current frontier models (haiku-4.5 + sonnet-4.5) the cheap and strong responses substantively converge on common-knowledge engineering prompts. The triage attempts during the 2026-05-14 walkthrough (list.insert complexity, dedup-preserving-order, K8s-vs-VM, underspecified database, GIL during HTTP I/O) all returned `agree=true`. The pytest test is the gate; this manual scenario is satisfied when the pytest passes. |
| 12.8 | `/mode list` | `/mode list` | Lists all workflows from `workflows.yaml` with `mode=` and `agents=[...]` columns. Includes the 5 Phase-12 workflows. |
| 12.9 | `/mode unknown` | `/mode phantom` | `Unknown workflow: phantom.` REPL state unchanged. |
| 12.10 | `/mode` is one-shot | `/mode brief_collaborative`, then any turn, then another turn | Second turn does NOT use brief_collaborative (back to routed default). |
| 12.11 | Unknown mode in workflows.yaml falls back | `uv run pytest tests/test_router.py::test_unknown_mode_in_workflows_yaml_falls_back_to_sequential` | Pass. router.workflow_mode logs a warning and returns `sequential` for unknown declared modes. |
| 12.12 | Help line includes `/mode` | `/foo` | Help banner lists `/mode <workflow>`. |
| 12.13 | Pytest passes | `uv run pytest tests/` | All green (~381 expected after Phase 12: Phase-11's 326 + 8 parallel + 13 evaluator (rank+agree+parsers) + 5 competitive + 4 collaborative + 4 debate + 7 speculative + 6 router + 6 /mode). |

## Phase 13 — Repair Agent Activated

Real failure detection plus multi-strategy recovery. `RepairAgent` classifies failures into `timeout | model_error | parse_error | content_rejection | precondition_missing | infinite_loop | unrecoverable` and returns an ordered `RecoveryPlan` via `plan_recovery`. The runner's sequential mode walks the full ladder (variant prompt → different model → smaller model + shorter prompt → peer replacement → abort), capped at `agents.repair.max_attempts=3`. All five fan-out modes (parallel, competitive, collaborative, debate, speculative) get peer replacement only — cancel-and-retry in `asyncio.gather` is still ambiguous, but a single peer substitution is a clean one-hop replacement. In speculative mode replacement is scoped to the cheap leader; the strong side already serves as a natural fallback. **`PRECONDITION_MISSING`** (Option A) is the new "input contract not met" bucket: `critic_no_candidate`, `memory_missing_input`, `execution_no_command` skip variant-prompt retries and lead with peer replacement, which is the smoke 12.4 fix.

`config/settings.yaml::agents.repair.peer_replacements` ships sensible defaults: `coding/research/critic → architect`, `architect ↔ operator`, `casual → operator`; `evaluator/memory/execution → null` (structurally unique). Override per-agent in settings.

`src/ubongo/memory/write_buffer.py` formalizes commit-on-success: the assistant-message commit is staged via `buf.stage(...)` and either committed (`buf.commit()`) on `result.ok` or dropped (`buf.drop()`) on failure. v0.1 has exactly one staged writer; Phase 19/20 agents can stage further mid-flight writes through the same seam. The vault `after_send` projection is already gated on `result.ok` via the Phase-7 queue contract.

New `repair_runs` table (FK → `workflow_runs.id`) persists one row per strategy attempted: `failure_kind`, `original_error`, `strategy_attempted`, `peer_agent`, `override_model`, `attempt_index`, `outcome (recovered | failed | aborted)`, timings. `/trace` renders an indented `repair: kind=… strategy=… outcome=… peer=… model=…` line under the failing `agent_runs` row. `workflow_runs.outcome='repaired'` lights up when any repair recovered AND the final result is OK.

When the ladder exhausts, `master.handle` returns a `Response` with `requires_user_decision=True` and a `repair_summary` dict (attempts/last_kind/last_strategy/failing_agent/last_error). The REPL prompts `Retry the same message? (y/n)`; `y` reissues the prior message once (no chaining; Phase 14 owns governance-gated retries), `n` / EOF returns to the prompt. One-shot prints the apology and exits `rc=1` (no prompt). The apology template interpolates the last failure's kind/strategy/agent/error so the user sees what went wrong.

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 13.1 | Parse-error recovery via variant prompt | `uv run pytest tests/test_runner.py::test_repair_passes_prompt_hint_to_agent_via_metadata` | Pass. The retry's `AgentInput.metadata['repair_prompt_hint']` is set; the agent's system prompt includes a `## Repair guidance` section. |
| 13.2 | Multi-strategy full ladder | `uv run pytest tests/test_runner.py::test_repair_walks_full_ladder_then_recovers` | Pass. Three `agent_runs` rows for the failing agent (one initial, two retries). The third retry uses `override_model` + `prompt_hint` + `max_tokens_cap=200` (smaller-model strategy). |
| 13.3 | Sequential peer replacement | `uv run pytest tests/test_runner.py::test_repair_peer_replacement_dispatches_peer_in_sequential` | Pass. Failing `critic` is replaced by `architect`; `agent_runs` shows critic (failure, retried=0), architect (success, retried=1). |
| 13.4 | Collaborative critic_no_candidate fix | REPL: `rm -f data/ubongo.db`; `/mode brief_collaborative`; `give me a brief on adopting microservices` | Merged document has all three role headings. `sqlite3 data/ubongo.db "SELECT strategy_attempted, peer_agent, outcome FROM repair_runs"` → `replace_with_peer, architect, recovered` for the critic row. Smoke 12.4 regression locked in. |
| 13.5 | Parallel peer replacement | `uv run pytest tests/test_runner.py::test_parallel_peer_replaces_failed_producer` | Pass. Failed producer's slot is filled by its peer; both run on the parallel turn. |
| 13.6 | `repair_runs` audit + outcome='repaired' | `uv run pytest tests/test_runner.py::test_repair_runs_persisted_on_successful_recovery` `uv run pytest tests/test_master.py::test_handle_workflow_run_outcome_repaired_when_recovery_succeeded` | Pass. `repair_runs` has one row with `outcome='recovered'`; `workflow_runs.outcome='repaired'`. |
| 13.7 | Ladder exhausted → ABORT row + apology | `uv run pytest tests/test_runner.py::test_repair_runs_persisted_with_abort_on_ladder_exhausted` `uv run pytest tests/test_master.py::test_handle_repair_summary_aggregates_attempts` | Pass. A final `repair_runs` row with `strategy_attempted='abort'` + `outcome='aborted'`. The Response carries `requires_user_decision=True` and a populated `repair_summary`. |
| 13.8 | Rollback regression | `uv run pytest tests/test_master.py::test_handle_failure_does_not_persist_assistant_message_or_vault` | Pass. On `repair_exhausted`: no `messages` row for the assistant turn, no vault note, queue row `source='error'`. |
| 13.9 | WriteBuffer contract | `uv run pytest tests/test_memory_write_buffer.py` | Pass. 13 cases (commit ordering, drop, double-commit raises, stage-after-commit raises, context-manager auto-drop, …). |
| 13.10 | `/trace` repair line rendering | `uv run pytest tests/test_repl_trace.py::test_render_trace_renders_repair_line_under_failing_agent` | Pass. The failing `critic` row is followed by an indented `repair: kind=precondition_missing strategy=replace_with_peer outcome=recovered peer=architect` line. |
| 13.11 | REPL y/n prompt | `uv run pytest tests/test_repl.py -k _prompt_repair_retry` | Pass. 5 cases covering `y`, `n`, anything-else-treated-as-n, EOF returns n, and case-insensitive whitespace tolerance. |
| 13.12 | Unrecoverable apology via one-shot | `rm -f data/ubongo.db`; `mv .env .env.bak`; `OPENROUTER_API_KEY=sk-or-v1-bogus bash -c 'uv run python -m ubongo send "hi" --persona casual'`; `mv .env.bak .env` | rc=1; stdout includes `couldn't recover` (the apology) and names the failing agent. `notification_queue` row has `source='error'`; no vault note. |
| 13.13 | `/agents` unchanged shape | REPL: `/agents` | Same rows as Phase 11 (Phase 13 didn't add or remove agents; from v0.1.5 the cumulative table includes `connector` too). |
| 13.14 | Competitive peer replacement | `uv run pytest tests/test_runner.py::test_competitive_peer_replaces_failed_candidate tests/test_runner.py::test_competitive_unrecoverable_candidate_not_replaced` | Pass. A failed candidate is replaced by its peer before ranking and can win; an unrecoverable failure (Repair returns ABORT) is not replaced and competition proceeds with the survivors. |
| 13.15 | Debate peer replacement | `uv run pytest tests/test_runner.py::test_debate_peer_replaces_failed_debater` | Pass. A failed debater is replaced by its peer; the peer's contribution reaches the synthesizer's transcript; one `repair_runs` row with `strategy='replace_with_peer'`. |
| 13.16 | Speculative leader replacement | `uv run pytest tests/test_runner.py::test_speculative_peer_replaces_failed_leader tests/test_runner.py::test_speculative_non_leader_failure_not_replaced tests/test_runner.py::test_speculative_unrecoverable_leader_falls_back_to_strong` | Pass. A failed leader (cheap) is replaced by its peer; a failed non-leader while the leader succeeds is left alone (Repair not consulted); an unrecoverable leader falls back to strong. |
| 13.17 | Pytest passes | `uv run pytest tests/` | All green (454 expected after Phase 13 + the fan-out peer-replacement completion: Phase-12's 382 + 66 Phase-13 + 6 competitive/debate/speculative peer-replacement). |

**Stale Phase-2/Phase-11 playbook entries patched in 13g:**

- Scenario 2.5: under Phase 13f the polite stdout text is no longer the generic `Sorry, I couldn't reach the model. Check the logs.` — when Repair's ladder runs (the common path with a bogus key), the apology template kicks in. The Phase-13 expectation is "stdout contains `couldn't recover`; rc=1; stderr has multiple `llm_attempt_failed` lines (count varies with how many strategies the ladder tries before ABORT)". The pytest suite is the gate for this path; the manual scenario is a sanity check.
- Scenario 11.9: rewrite the query to `SELECT COUNT(*) FROM workflow_runs WHERE started_at > '<before>'` using a timestamp captured before the `/exec` block. The Phase-12 workflow JSON contains `"execution_mode"` so `LIKE '%exec%'` matches every row and is no longer a useful filter.

## Phase 14 — Risk + Confidence Scoring

Start of Tier 4 (Governance). The decision matrix actually decides. Three score modules feed `governance/decision.py::decide()`, which loads `config/governance.yaml` and returns one of `auto | ask_clarification | require_approval | reject`. **risk** (`governance/risk.py`) takes the higher of the classifier's rating and a destructive-keyword scan of the message — so "delete the entire vault" reaches `destructive` even when the small classifier model under-rates it. **confidence** (`governance/confidence.py`) is the Evaluator's score, classifier confidence as fallback. **reversibility** (`governance/reversibility.py`) is `irreversible` when the workflow runs the `execution` agent or an irreversible skill, else `reversible`.

The matrix runs in priority order — safety before quality before clarity: (1) destructive risk → `require_approval`; (2) high risk + irreversible → `require_approval`; (3) evaluator confidence below the reject floor → `reject`; (4) `command` turn with low classifier confidence → `ask_clarification`; (5) otherwise → `auto`. Every threshold lives in `config/governance.yaml`; nothing is hardcoded.

`master.handle` acts on all four actions: a `_GATED_MESSAGES` map replaces the response with a canned message for `reject` / `ask_clarification` / `require_approval` (the interactive y/n approval flow is Phase 15). `governance_decisions` now persists `reversibility` (no longer NULL) plus the scored `risk`/`confidence`. `/decisions` and `/trace` show the `rev` column. The new `/policy` command prints the live matrix. The Phase-10 `CRITIC_LOW/HIGH` constants moved into `governance.yaml::thresholds.critic_band`; the `governance:` block left `settings.yaml` entirely (single config home).

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 14.1 | governance.yaml loads | `uv run pytest tests/test_config.py -k governance` | Pass. `load_governance()` reads the matrix; cached; missing file raises. |
| 14.2 | Risk scoring + keyword backstop | `uv run pytest tests/test_governance_risk.py` | Pass. `score_risk` returns the higher of classifier risk and a destructive-keyword hit; case-insensitive. |
| 14.3 | Confidence scoring | `uv run pytest tests/test_governance_confidence.py` | Pass. Evaluator score preferred; classifier confidence is the fallback; `0.0` is a real verdict. |
| 14.4 | Reversibility scoring | `uv run pytest tests/test_governance_reversibility.py` | Pass. `execution` agent or an irreversible skill → `irreversible`; every other turn `reversible`. |
| 14.5 | Decision matrix — all five rules | `uv run pytest tests/test_governance_decision.py` | Pass. One test per matrix cell: destructive → require_approval, high+irreversible → require_approval, low evaluator confidence → reject, low-confidence command → ask_clarification, else auto. |
| 14.6 | Auto-approve (live) | `rm -f data/ubongo.db`; `uv run python -m ubongo send "what is a write-ahead log" --persona architect`; `sqlite3 data/ubongo.db "SELECT action, reversibility FROM governance_decisions"` | Substantive answer delivered. `action='auto'`, `reversibility='reversible'`. |
| 14.7 | Require approval (live) | `uv run python -m ubongo send "delete the entire vault" --persona casual` | stdout is the approval-required message ("not proceeding without explicit approval"); the real answer is NOT delivered. `sqlite3 data/ubongo.db "SELECT action, risk FROM governance_decisions ORDER BY id DESC LIMIT 1"` → `require_approval, destructive` (the keyword backstop escalated risk). |
| 14.8 | Reject low confidence | `uv run pytest tests/test_master.py::test_low_confidence_rejects` | Pass. Mock evaluator returns 0.1; `governance_decisions.action='reject'`; response is `_REJECT_MESSAGE`. |
| 14.9 | Ask clarification | `uv run pytest tests/test_master.py::test_handle_ask_clarification_on_low_confidence_command` | Pass. A `command` turn with low classifier confidence → `action='ask_clarification'`; response is `_CLARIFICATION_MESSAGE`. |
| 14.10 | Require approval persists scored signals | `uv run pytest tests/test_master.py::test_handle_require_approval_on_destructive_keyword tests/test_master.py::test_handle_persists_reversibility_not_null` | Pass. `governance_decisions` rows carry `action`, `risk`, and a non-NULL `reversibility`. |
| 14.11 | `/policy` prints the matrix | REPL: `/policy` | Prints the 5 priority-ordered rules, thresholds (reject/clarification floors, critic band, auto-route min confidence), `require_approval` rules, and the destructive-keyword list. |
| 14.12 | `/decisions` + `/trace` show reversibility | After 14.6: REPL `/decisions 1` and `/trace 1` | `/decisions` row includes a `rev` column; `/trace` governance line ends with `rev=reversible`. |
| 14.13 | `/policy` in the help banner | REPL: `/foo` | Help banner lists `/policy` between `/decisions` and `/agents`. |
| 14.14 | Pytest passes | `uv run pytest tests/` | All green (490 expected after Phase 14: Phase-13's 454 + ~36 governance/matrix/policy/master tests). |

## Phase 15 — Approval Gates + Sandboxing

End of Tier 4 (Governance). Phase 14's `require_approval` decision becomes a real
decision point. `governance/approval.py` turns a `require_approval` `Decision`
into an `ApprovalRequest` (a one-line summary + a "why" paragraph), which
`master.handle` attaches to the `Response`. The REPL prompts `Approve? (y/n/why)`:
`why` prints the explanation and re-prompts; `n` records the decline and aborts;
`y` records approval and re-issues the turn with `approved=True` so the real
answer is delivered (the re-run's `governance_decisions` row reads `action=auto`,
reason `approved_by_user`). The choice persists in
`governance_decisions.approval_response` via `store.update_governance_decision`.
One-shot is non-interactive — a `require_approval` turn prints the gated message
and exits `rc=1`.

The Execution Agent's sandbox is hardened: the parent resolves each allowlisted
command to an absolute path at import (`_PROGRAM_PATHS`) and the child subprocess
runs with `PATH=""` — it cannot spawn further programs by bare name. `_check_paths`
gains a filesystem allowlist: any absolute-path argument must resolve inside the
repo tree. `docs/SECURITY.md` documents the full contract and its known v0.1
limits (no OS-level isolation; network governed by the allowlist, not blocked).

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 15.1 | Approval module | `uv run pytest tests/test_governance_approval.py` | Pass. `build_request` / `explain` produce the summary + why paragraph; unknown reasons fall back gracefully. |
| 15.2 | Approval REPL prompt + persistence | `uv run pytest tests/test_repl_approval.py` | Pass. `_prompt_approval` handles y/n/why/EOF; `store.update_governance_decision` patches `approval_response`. |
| 15.3 | Approval yes (live) | `rm -f data/ubongo.db`; REPL: `delete the entire vault`, then `y` | After the gated message + `Approve? (y/n/why)`, `y` re-issues the turn and the real answer is delivered. `sqlite3 data/ubongo.db "SELECT action, approval_response FROM governance_decisions ORDER BY id"` → first row `require_approval, y`; second row `auto, NULL` (the approved re-run). |
| 15.4 | Approval no (live) | REPL: `delete the entire vault`, then `n` | `Aborted; nothing was done.`; back at the prompt. `governance_decisions.approval_response='n'` for the gated row. |
| 15.5 | Approval why (live) | REPL: `delete the entire vault`, then `why` | A one-paragraph risk explanation prints (names risk/reversibility, echoes the request); `Approve? (y/n/why)` re-prompts. |
| 15.6 | One-shot require_approval exits rc=1 | `uv run python -m ubongo send "delete the entire vault" --persona casual` | stdout is the gated message; `rc=1`; no interactive prompt. |
| 15.7 | Sandbox empty PATH + filesystem allowlist | `uv run pytest tests/test_sandbox.py` | Pass. Child PATH is `""`; an out-of-repo absolute path is refused; an in-repo absolute path runs; `_PROGRAM_PATHS` resolves to absolute. |
| 15.8 | Sandbox path violation | REPL: `/exec cat /etc/passwd` | `Refused: path fragment '/etc' rejected …` — no read. (`/exec cat /tmp/x` → `Refused: absolute path … outside the repo sandbox`.) |
| 15.9 | Sandbox timeout | `uv run pytest tests/test_sandbox.py::test_timeout_returns_result_with_exit_neg_one` | Pass. A child that sleeps past the timeout is killed; `exit_code=-1`, stderr `(timed out)`. |
| 15.10 | Network blocked | REPL: `/exec curl https://example.com` | `Refused: program 'curl' not in allowlist`. |
| 15.11 | Master approval integration | `uv run pytest tests/test_master.py -k approval` | Pass. A `require_approval` turn attaches the `approval` payload; `approved=True` bypasses the gate (`action=auto`); a normal turn has no payload. |
| 15.12 | Pytest passes | `uv run pytest tests/` | All green (515 expected after Phase 15: Phase-14's 491 + ~24 approval/sandbox tests). |

## Phase 16 — Variant Generation

Tier 5 (Self-Improvement) opens with on-demand variant generation. `/optimize <target>` mutates an evolvable persona prompt into `population_size` (8) strategy-diverse alternates and persists each as an `evolution_lineage` row. Five strategies (paraphrase, prune, expand, recombine, perturb_temperature) are allocated round-robin, so a run is never all-paraphrase; `perturb_temperature` is a pure-metadata variant (same text, a sampling-temperature delta, no LLM call). No evaluation, no loop, no promotions — those are Phases 17–19. The schema (`evolution_lineage`) and the `settings.evolution` block already shipped, so there is no migration. `/optimize` is a direct REPL tool (like `/exec`): no `master.handle`, no governance, no enqueue.

| # | Step | Command | Expected |
| --- | --- | --- | --- |
| 16.1 | List evolvable targets | `/optimize` | Lists `persona:architect`, `persona:operator`, `persona:casual`. |
| 16.2 | Generate variants | `/optimize persona:casual` | Prints "8 variant(s) for persona:casual, generation 1"; eight numbered lines, each `#<id> <strategy>: <preview>`. |
| 16.3 | Diversity | inspect 16.2 output | Not all paraphrases; at least four distinct strategy labels; one `perturb_temperature (Δtemp=…)`. |
| 16.4 | Plausibility | read the previews | The casual-voice alternates read as real prompts, not gibberish. |
| 16.5 | Lineage persisted | `sqlite3 data/ubongo.db "SELECT generation, COUNT(*) FROM evolution_lineage WHERE target='persona:casual' GROUP BY generation"` | One row: `1\|8`. |
| 16.6 | Generation increments | `/optimize persona:casual` again, then re-run 16.5 | Two rows: `1\|8` and `2\|8`. |
| 16.7 | Parent is NULL (no promotion yet) | `sqlite3 data/ubongo.db "SELECT DISTINCT parent_id FROM evolution_lineage WHERE target='persona:casual'"` | NULL only. |
| 16.8 | Unknown target | `/optimize persona:bogus` | "Unknown target: persona:bogus." + the target list; no rows written. |
| 16.9 | Help | `/help` or any unknown command | Usage line includes `/optimize <target>`. |
| 16.10 | Pytest passes | `uv run pytest tests/` | All green (549 expected after Phase 16: Phase-15's 515 + 34 evolution/optimize tests). |

## Phase 17 — Sandboxed Evaluation + Fitness

The optimize→evaluate half of the GP loop closes. `/evaluate <target>` scores a target's latest generation of variants against the held-out conversation set (`tests/manual/fixtures/sample_conversations.json`, 33 anonymized samples) and prints a fitness-ranked leaderboard. For each sample a variant generates a response (system prompt = `UBONGO.md` + variant text, no skill/memory layers, for isolation), then one judge call returns `{quality, hallucination, would_user_correct}`. Fitness is a cohort-normalized weighted sum (`evolution.fitness_weights`); ties break on `lineage_id` ascending. A `CallBudget` (seeded from `evolution.max_calls_per_hour`) throttles cost and returns partial results; `evolution.samples_per_eval` (default 5) caps samples per variant. The harness has **no** side effects — only `evolution_evaluations` rows are written. `EvaluatorAgent` and governance are untouched. Like `/optimize`, `/evaluate` is a direct tool (no `master.handle`).

| # | Step | Command | Expected |
| --- | --- | --- | --- |
| 17.1 | List evaluable targets (none yet) | fresh DB, `/evaluate` | "No evaluable targets. Run /optimize..." |
| 17.2 | Seed a generation | `/optimize persona:casual` | 8 variants written (Phase 16). |
| 17.3 | List evaluable targets | `/evaluate` | Lists `persona:casual`. |
| 17.4 | Evaluate | `/evaluate persona:casual` | "Leaderboard for persona:casual, generation 1 ..."; ranked rows `#<id> <strategy> fitness=… success=… halluc=… corr=… cost=…tok lat=…ms`. |
| 17.5 | Fitness ordering | inspect 17.4 | Rows sorted by fitness descending; ties resolve to lower `#lineage_id` first. |
| 17.6 | Persisted | `sqlite3 data/ubongo.db "SELECT COUNT(*) FROM evolution_evaluations e JOIN evolution_lineage l ON l.id=e.lineage_id WHERE l.target='persona:casual'"` | Equals the number of variants scored. |
| 17.7 | Cost cap throttles | set `evolution.max_calls_per_hour: 10` in a scratch config; `/evaluate persona:casual` | Fewer variants scored; a "skipped by the call budget" note prints; partial leaderboard still shown. |
| 17.8 | Hallucination signal | inspect a low-quality variant's row | `halluc` column is elevated relative to a clean variant (the trap samples drive it). |
| 17.9 | Determinism | run 17.4 twice on identical inputs (mock or fixed seed) | Leaderboard order is stable. |
| 17.10 | Unknown target | `/evaluate persona:bogus` | "Unknown target: persona:bogus." + target list; no rows written. |
| 17.11 | Help | `/help` or unknown command | Usage line includes `/evaluate <target>`. |
| 17.12 | Pytest passes | `uv run pytest tests/` | All green (583 expected after Phase 17: Phase-16's 549 + 34 evaluation tests). |

## Phase 18 — GP Loop (autonomous)

The two halves connect into a continuous background loop. When `evolution.enabled=true`, the REPL starts a daemon thread running `EvolutionLoop`; it comes up **paused** (persisted status), so nothing spends until `/evolution resume`. Each cycle picks the stalest target (round-robin), generates a generation seeded from the previous one's champion survivor (cross-generation lineage; `parent_id` set), evaluates it under a shared `CallBudget`, and records an `evolution_runs` row. The throttle is now a real rolling-hour window (sum `calls_spent` over the trailing hour); `evolution.cron` is `null` (continuous) or an integer = min seconds between cycles. Progress is DB-derived, so killing the REPL mid-generation and restarting resumes the last completed generation (an unevaluated latest generation is re-evaluated, not regenerated). `/evolution status|pause|resume|off` control it. Promotions (acting on winners) are Phase 19. Two new tables (`evolution_runs`, `evolution_state`) via `CREATE TABLE IF NOT EXISTS` — no ALTER.

| # | Step | Command | Expected |
| --- | --- | --- | --- |
| 18.1 | Loop starts paused | `evolution.enabled: true`; launch REPL; `/evolution status` | status=paused; per-target generations listed; throttle 0/N. |
| 18.2 | Resume | `/evolution resume` | "resumed (status=running)". |
| 18.3 | Loop runs | wait a few minutes; `/evolution status` | ≥1 generation completed; calls-in-last-hour climbing; recent cycles listed. |
| 18.4 | Round-robin | after 3+ cycles | generations advance across all three persona targets (lineage timestamps interleave). |
| 18.5 | Cross-generation lineage | `sqlite3 data/ubongo.db "SELECT DISTINCT parent_id FROM evolution_lineage WHERE target='persona:architect' AND generation=2"` | non-NULL (gen 2 seeded from a gen-1 survivor). |
| 18.6 | Pause | `/evolution pause`; wait; `/evolution status` | no new generations after the in-flight one finishes. |
| 18.7 | Throttle respected | set `max_calls_per_hour: 5`; resume; watch | a cycle stays ≤5 calls in the window; `/evolution status` throttle ≤5/5; further cycles skipped until the window frees. |
| 18.8 | Cron pacing | set `cron: 120`; resume | cycles start no more often than every 120s. |
| 18.9 | Crash recovery | kill the REPL mid-cycle; restart; `/evolution status` | resumes from the last completed generation; no duplicate generation; an interrupted generation is re-evaluated. |
| 18.10 | Off | `/evolution off` | loop idles until `resume`. |
| 18.11 | Disabled | `evolution.enabled: false`; launch; `/evolution status` | note that the loop thread does not start; `/evolution resume` warns to enable it. |
| 18.12 | Pytest passes | `uv run pytest tests/` | All green (623 expected after Phase 18: Phase-17's 584 + 39 loop/selection/throttle/control/recovery tests). |

## Phase 19 — GP Targets Expanded + Promotions

End of Tier 5. The self-improvement loop closes: the loop proposes a promotion when a generation's champion beats the active baseline by `evolution.promotion_margin`; the user approves/rejects/rolls back via `/improvements`; approval performs a **live swap** so behavior actually changes. Audit log at `vault/system/evolution-audit.md`. Evolvable targets expand beyond persona prompts via a target *kind* (`prompt` vs `config`): `routing:default`, `toolchain:<workflow>`, and `retry:repair`. Config variants are deterministic, validated structural mutations; routing/tool-chain variants are evaluated by running the real pipeline under an in-memory override with zero side effects and judging the responses; retry uses a documented structural proxy. Live swap reads `active_evolutions`: `build_system_prompt` (persona), `router.route_workflow` (routing), `router.workflow_agents` (tool-chain). No schema migration (`pending_promotions` / `active_evolutions` already ship).

| # | Step | Command | Expected |
| --- | --- | --- | --- |
| 19.1 | Personas + config targets evolvable | `/optimize` | Lists `persona:*` plus `routing:default`, `toolchain:<wf>`, `retry:repair`. |
| 19.2 | Loop proposes | enable + `/evolution resume`; wait until a champion beats baseline; `/improvements` | Non-empty: pending promotion(s) with a diff + fitness delta. |
| 19.3 | Prompt diff | `/improvements` after a persona generation | A unified diff of the candidate persona body vs the active/file body; `fitness base → champ`. |
| 19.4 | Approve persona → live swap | `/improvements approve <id>`; then ask a normally-classified question | Reply reflects the promoted prompt; `sqlite3 data/ubongo.db "SELECT * FROM active_evolutions"` has the persona row; audit row appended. |
| 19.5 | Routing variant | `/optimize routing:default`; `/evaluate routing:default` | Structural routing variants generated; leaderboard with fitness (variants ran the real pipeline + judge). |
| 19.6 | Approve routing → live swap | `/improvements approve <id>` for a `routing:default` promotion; then a turn that the new rule re-routes | The turn routes to the new workflow (scenario 4); `active_evolutions` has the `routing:default` row. |
| 19.7 | Reject | `/improvements reject <id>` | Recorded; the queue shrinks; no `active_evolutions` change. |
| 19.8 | Rollback | `/improvements rollback persona:architect` (after an approve) | Reverts to the file body; live swap off; audit row appended; `active_evolutions` row gone. |
| 19.9 | Audit log | `cat vault/system/evolution-audit.md` | One row per decision (approve / reject / rollback) with target, lineage, fitness delta. |
| 19.10 | Config eval is side-effect free | after `/evaluate routing:default`, compare `workflow_runs` / `agent_runs` counts before/after | Unchanged — the isolated evaluator writes only `evolution_evaluations`. |
| 19.11 | Invalid config variant rejected | (unit) malformed routing/tool-chain/retry | `apply_variant` raises; generation drops it; nothing malformed persisted. |
| 19.12 | Help | `/help` or unknown command | Usage line includes `/improvements`. |
| 19.13 | Pytest passes | `uv run pytest tests/` | All green (672 expected after Phase 19: Phase-18's 623 + 49 promotion/live-swap/config tests). |

## Phase 20 — Embeddings + Graph

Start of Tier 6. Recall stops being recency-only: `store.recall(conversation_id, query)` embeds the current query and retrieves the most similar prior messages **outside** the recency window via `sqlite-vec`, returned on `RecallContext.semantic_messages` and folded into the turn context by the runner as a labelled "[Relevant earlier context]" block. The Memory Agent's message write (`store.append_message`, the one place every user/assistant turn is born) indexes the message idempotently — a text-hash sidecar (`embedding_meta`) means re-indexing unchanged text makes no embed call. Daily-note `[[wikilinks]]` populate `vault_links`, queryable via `memory/graph.py` (`neighbors` / `backlinks` / bounded `traverse`). A new `/recall [query]` command surfaces all of it. **Graceful degradation is first-class**: with `memory.embeddings.enabled: false`, a blocked extension, or a down endpoint, everything collapses to recency-only with no errors, and an embedding never blocks a message commit. Vec tables (`vec_messages`, `vec_vault`) are created lazily behind a `vec_available()` guard, so embeddings-off runs never load the extension. No destructive migration (one additive `embedding_meta` table).

| # | Step | Command | Expected |
| --- | --- | --- | --- |
| 20.1 | Semantic recall surfaces old context | `rm -f data/ubongo.db`; seed a caching discussion, then drive 12+ unrelated turns; `ubongo send "remember our caching discussion"` | the reply reflects the old caching turns even though they are outside the last-N window. |
| 20.2 | `/recall` shows recency + semantic | after 20.1, REPL `/recall caching` | prints the recency window, a "semantic hits" block with the old caching turn (`#id`), and vault-graph neighbors of today's note. |
| 20.3 | Embedding idempotency | re-run `ubongo send` on an unchanged conversation; watch logs | no new embed calls for unchanged messages (`embedding_meta.text_hash` unchanged); `sqlite3 data/ubongo.db "SELECT COUNT(*) FROM embedding_meta"` stable. |
| 20.4 | Vault graph from `[[wikilink]]` | send a turn containing `[[caching-notes]]`; `sqlite3 data/ubongo.db "SELECT source_path, target_path FROM vault_links"` | a `wikilink` row appears with today's note → `caching-notes`; `/recall` neighbors include it. |
| 20.5 | Without embeddings (graceful) | set `memory.embeddings.enabled: false` (or `UBONGO_DISABLE_EMBEDDINGS=1`); restart; `/recall caching` | recency-only; "(embeddings disabled — recency only)"; no errors; turns still work. |
| 20.6 | Extension-blocked (graceful) | (if a platform blocks `enable_load_extension`) launch normally | `vec_available()` False; recency-only; warning logged once, no crash. |
| 20.7 | Help | `/help` or unknown command | usage line includes `/recall [query]`. |
| 20.8 | Pytest passes | `uv run pytest tests/` | All green (701 expected after Phase 20: Phase-19's 676 + 25 embeddings/recall/graph/recall tests). |

## Phase 21 — Bidirectional Vault Sync + Audit

The final v0.1 phase. The one-way vault projection becomes bidirectional: when `vault.sync.enabled: true`, a no-dependency polling daemon (`VaultWatcher`, mirroring the GP loop) scans `vault/daily/*.md` every `poll_interval_s` and ingests **external** edits you make in Obsidian — re-embedding them into `vec_vault`. It tells its own writes from your edits via `vault_state` (the hash the system last wrote): disk hash matches → system write (skip, no echo); differs → external edit → ingest, and on a system-managed note, queue a conflict. `/conflicts` lists and resolves collisions (keep-mine / keep-theirs / merge); for append-only daily notes the practical resolution is "coexist" (honest: the keep-mine/merge paths exist for correctness, not heavy use). Governance + evolution + sync decisions unify into `vault/system/audit.md`, tailed by `/audit [category] [N]`. `/reload` now also hot-reloads settings (`config.reload()` before personas, so a `models.*` edit applies next turn). The watcher is off by default and started/stopped by the REPL alongside the GP loop. Additive tables only (`vault_state`, `vault_conflicts`); `vec_vault` already existed — no destructive migration, no new dependency.

| # | Step | Command | Expected |
| --- | --- | --- | --- |
| 21.1 | Vault edit ingestion | set `vault.sync.enabled: true`; launch REPL; edit a daily note in a text editor; wait ~poll_interval_s | logs show `index_vault` / a `[sync]` audit row; the edit is re-embedded (no crash). |
| 21.2 | No echo on own writes | send a normal turn (system appends to the daily note); watch the watcher | the system's own append is NOT re-ingested (`vault_state` hash matches). |
| 21.3 | Conflict queued | edit a daily note the system manages, externally; `/conflicts` | one open conflict listed with the note path. |
| 21.4 | Conflict resolve | `/conflicts resolve <id> keep-theirs` | "resolved"; queue shrinks; a `[sync]` audit row appended. |
| 21.5 | Unified audit | after a gated turn (`delete the entire vault`) and a promotion; `/audit` | rows under `[governance]` and `[evolution]`; `/audit governance` filters. |
| 21.6 | Settings hot-reload | edit `models.casual` in settings.yaml; `/reload`; then a casual turn | "Reloaded settings, …"; the next casual turn uses the new model. |
| 21.7 | Sync off (default) | `vault.sync.enabled: false`; launch | the watcher does not start; turns work normally; no ingestion. |
| 21.8 | Help | `/help` or unknown command | usage includes `/audit [category]` and `/conflicts`. |
| 21.9 | Pytest passes | `uv run pytest tests/` | All green (723 expected after Phase 21: Phase-20's 701 + 22 sync/audit/reload tests). |
| 21.10 | Full cumulative smoke | walk the entire playbook (Phases 0–21) | passes end-to-end without manual fixup — **v0.1 certification**. |

## Post-v0.1 — Self-authored skills (authoring)

The self-extension experiment ([ADR-0013](../../docs/adr/0013-self-authored-skills-quarantine-and-approval.md)): Ubongo drafts brand-new skills behind a human approval boundary. Drafts are quarantined in `config/skills_candidates/` (which `skills.py` does not scan), so nothing is discoverable until you approve it via `/skill-candidates approve`. A command-skill risk floor and static `sandbox.validate_command` checks are enforced in code. The autonomous `AuthoringLoop` daemon boots paused, is throttled, infers recurring capability gaps, and only ever drafts — approval stays manual. Off-switches `UBONGO_DISABLE_AUTHORING_EVAL` / `UBONGO_DISABLE_AUTHORING` keep the suite offline and daemon-free.

| # | Step | Command | Expected |
| --- | --- | --- | --- |
| A.1 | Daemon boots paused | launch REPL; `/authoring status` | "Authoring daemon: paused"; never auto-spends on launch. |
| A.2 | Manual draft | `/author summarize a git diff into release notes` | a candidate is drafted, risk-floored if it carries a command, given an estimated quality, and **quarantined** (status: quarantined). |
| A.3 | Quarantine isolation | `/skills` | the just-drafted skill is NOT listed (invisible to the runtime until approved). |
| A.4 | List candidates | `/skill-candidates` | the draft is listed with status `draft`, source, and quality. |
| A.5 | Approve → live | `/skill-candidates approve <id>`; then `/skills` | "Approved … now in /skills"; the skill now appears in `/skills`. |
| A.6 | Use it | `/skill <name>`; send a matching message; `/trace 1` | the response reflects the skill; the trace shows `skill=<name>` on the turn. |
| A.7 | Rollback | `/skill-candidates rollback <name>`; `/skills` | "Rolled back …"; the skill is gone from `/skills` (or restored to the prior version if one existed). |
| A.8 | Versioned backup | re-`/author` the same name; approve; check `config/skills_backups/<name>/` | a timestamped backup of the prior version exists; the new version is live; `rollback` restores the prior version intact. |
| A.9 | Risk floor / sandbox | `/author a skill that runs a shell command` | a command-bearing draft is forced to `risk: medium` / `irreversible`; an unsafe command (non-allowlisted program, metacharacter, path traversal) is rejected at draft. |
| A.10 | Reject | `/skill-candidates reject <id>` | "Rejected …"; the draft stays in quarantine, never registered. |
| A.11 | Autonomous daemon (live) | seed recurring turns whose intent matches no skill; `/authoring resume`; wait a cycle; `/authoring status` + `/skill-candidates` | the daemon drafts a `src=auto` candidate into quarantine; it is NOT auto-approved. |
| A.12 | Daemon control | `/authoring pause`, `/authoring off`, `/authoring resume` | status flips and persists across restart (comes back in the persisted state, paused on first ever launch). |
| A.13 | Audit | `/audit authoring` | rows under `[authoring]` for drafts and decisions. |
| A.14 | Pytest passes | `uv run pytest` | all green (the six `test_authoring_*` suites included). |

## Post-v0.1 — Local profiler (candidate 10)

The local profiler (`ubongo.profiling`, [Plans/10-local-profiler.md](../../Plans/10-local-profiler.md)): `/profile` aggregates the `workflow_runs` / `agent_runs` rows every turn already persists into summary and per-agent / per-model / per-mode breakdowns — on demand, read-only, no new tables. `/profile cpu on` (or `ubongo send --profile`) wraps the turn's `master.handle` in stdlib `cProfile`, dumping `data/profiles/turn-<ts>.prof` plus a top-25 cumulative summary; profiling is best-effort and never breaks a turn. Zero overhead when off.

| # | Step | Command | Expected |
| --- | --- | --- | --- |
| P.1 | Summary | `/profile` (after at least one turn) | turn count, avg + p95 latency ms, tokens in/out, slowest agent; on a fresh db: "No runs recorded yet." |
| P.2 | Per-agent breakdown | `/profile agents` | a table with runs, avg/p95 ms, tokens, fail%, retried per agent, most expensive first. |
| P.3 | Per-model / per-mode | `/profile models`, `/profile modes` | same shape grouped by model / execution mode (mode table: workflow wall latency, no token columns). |
| P.4 | Last-N window | `/profile agents 1` | only agents from the most recent workflow run. |
| P.5 | CPU arm + turn | `/profile cpu on`; send a normal turn | the response prints as usual, then a `CPU profile written to data/profiles/turn-<ts>.prof` report with the top-25 cumulative table; `/profile cpu off` disarms; `/profile cpu status` reports the state. |
| P.6 | One-shot CPU profile | `ubongo send "hello" --profile` (message first: a bare `--profile` before the message would consume it as the flag value) | same report after the reply; a new `.prof` under `data/profiles/`. |
| P.7 | Never breaks a turn | make `data/profiles/` unwritable (`chmod 444`); profiled turn | the turn still answers; the report degrades to a logged warning, no crash. Restore permissions after. |
| P.8 | Bad args | `/profile bogus`, `/profile cpu maybe`, `/profile mem maybe` | usage line, including in `/help`'s banner (`/profile [agents\|models\|modes\|cpu\|mem] [N]`). |
| P.9 | Memory arm + report (candidate 11) | `/profile mem on`; send a turn or two; `/profile mem` | "Memory profiling armed" with the overhead warning; the report shows traced now/peak + process RSS and the top allocation-growth sites since the baseline (`file:line`, size and block deltas). |
| P.10 | Memory disarm | `/profile mem off`; `/profile mem` | "Memory profiling off."; the bare report then answers "Memory profiling is off. /profile mem on to take a baseline first." `/profile mem status` tracks the armed state throughout. |
| P.11 | Startup switch — flag (candidate 12) | `./start-ubongo.sh --profile mem` (or `--profile`, `--profile all`) | the REPL banner is followed by "Profiling armed at startup: …"; `/profile mem status` (and/or `/profile cpu status`) reports on; `/profile … off` disarms mid-session. |
| P.12 | Startup switch — .env | set `UBONGO_PROFILE=cpu` in `.env`; `./start-ubongo.sh` | armed exactly as P.11; `./start-ubongo.sh --profile off` overrides the env back to off; an invalid value (e.g. `UBONGO_PROFILE=bogus`) logs a warning and starts unarmed. |
| P.13 | One-shot mem | `ubongo send --profile mem "hello"` (a valued flag composes either side; bare `--profile` must come after the message) | the reply, then a "Memory growth since baseline" report for that turn; `all` prints both reports. |
| P.14 | Service control | `./ubongo-ctl.sh start`, then `status`, `restart`, `stop` | start backgrounds the web UI (pid in `data/ubongo-web.pid`, log `data/ubongo-web.log`); status exits 0 with the pid; restart swaps the pid; stop terminates and removes the pidfile; a second `stop` reports "Not running.". |
| P.15 | systemd (Pi only) | follow the comments in `deploy/ubongo-web.service` | unit starts the web UI, survives reboot with lingering enabled; `journalctl --user -u ubongo-web -f` shows the app log. |
| P.16 | Pytest passes | `uv run pytest tests/` | all green (`tests/test_profiling.py` included). |

## Post-v0.1 — MCP server channel (candidate 13, v0.1.4)

The MCP channel ([Plans/13-mcp-server.md](../../Plans/13-mcp-server.md), ADR-0015): Ubongo as an MCP server, the fourth additive channel. Tools `ubongo_send` (a full governed turn through `master.handle`, exactly like one-shot; a gated turn returns `gated=true` and is never approvable over MCP) and `ubongo_recall` (read-only); resources `ubongo://vault/daily/today` and `ubongo://audit` (read-only). Transports: stdio (`python -m ubongo mcp`) and streamable HTTP (`./start-ubongo-mcp.sh`, LAN no-auth posture like the web UI). Optional extra: `./install.sh --mcp` / `uv sync --extra mcp`.

| # | Step | Command | Expected |
| --- | --- | --- | --- |
| M.1 | Missing extra is friendly | (without the extra) `python -m ubongo mcp` | rc 1; "The MCP dependency is not installed." with the install hint; no traceback. |
| M.2 | stdio handshake | add to a local client (e.g. Claude Code): `{"command": "<repo>/.venv/bin/python", "args": ["-m", "ubongo", "mcp"]}`; list tools | client shows `ubongo_send` + `ubongo_recall` and the two `ubongo://` resources. |
| M.3 | HTTP service | `./ubongo-ctl.sh start mcp`; point an MCP client at `http://<host>:8765/mcp` | tools/resources listed; `./ubongo-ctl.sh status mcp` exits 0; `stop mcp` terminates. |
| M.4 | Full governed turn | call `ubongo_send` with `{"message": "say hello", "persona": "casual"}` | a real composed response; the turn shows up in `/trace`, the queue, and the daily note exactly like a typed turn. |
| M.5 | Gate is not approvable | call `ubongo_send` with `{"message": "delete the entire vault"}` | the canned approval-required text with `gated: true`; no approval payload; `governance_decisions` row `require_approval` persisted. |
| M.6 | Read-only memory | call `ubongo_recall` `{"query": "<topic>"}`; read both resources | recency + semantic rows; the daily note and audit tail render; nothing is written (`messages` count unchanged). |
| M.7 | systemd (Pi only) | follow the comments in `deploy/ubongo-mcp.service` | unit serves the LAN and survives reboot; do not run it and `ubongo-ctl.sh ... mcp` together. |
| M.8 | Pytest passes | `uv run pytest tests/` | all green (`test_mcp_service.py` + `test_mcp_server.py` included; the server suite skips without the extra). |

## Post-v0.1 — MCP client / Connector agent (candidate 20, v0.1.5)

The outbound half ([Plans/20-mcp-client.md](../../Plans/20-mcp-client.md), ADR-0016): the **Connector agent** (ninth worker, `composer=False`) plans tool calls over the servers declared in `settings.yaml::mcp.servers` and returns the results as Findings. Reached only via `/mode connector_session` (not auto-routed). Governance: any connector workflow is irreversible; risk escalates to the highest enabled server's declared `risk`. Tool calls append `[mcp]` audit rows.

| # | Step | Command | Expected |
| --- | --- | --- | --- |
| C.1 | Workflow declared, not routed | `/mode list`; send a plain technical question without `/mode` | `connector_session  mode=sequential agents=[connector,architect]` listed; the plain turn never routes to it. |
| C.2 | Honest no-config finding | with `mcp.servers: {}`: `/mode connector_session`, any message | the reply notes no external servers are enabled; turn succeeds (architect answers unaided). |
| C.3 | Loop-back end to end | `./ubongo-ctl.sh start mcp`; enable a `selfback` server pointing at `http://127.0.0.1:8765/mcp`; `/mode connector_session`, ask it to recall something | the Connector lists Ubongo's own tools, calls `ubongo_recall`, and the architect composes from the result; `/trace 1` shows `connector` then `architect`; `/audit mcp` has the call row. |
| C.4 | Governance posture | after C.3: `/decisions 1` | `rev=irreversible`; risk = the server's declared level (low for the loop-back ⇒ `action=auto`). Set the server's `risk: high` and repeat: the turn gates with `Approve? (y/n/why)`. |
| C.5 | Dead server degrades | point the server at a closed port; `/mode connector_session` turn | the Connector fails, Repair replaces it with the architect (`/trace` shows the repair line), and the turn still answers. |
| C.6 | Compendium (when it exists) | enable the real `compendium:` entry; `/mode connector_session`, ask a Compendium-shaped question | tool results from Compendium appear in the composed answer; `[mcp]` audit row names the server/tool. |
| C.7 | Pytest passes | `uv run pytest tests/` | all green (`test_mcp_client.py`, `test_agents_connector.py`, the governance additions). |
