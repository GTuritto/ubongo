# Handoff: Phases 0–5 merged on main; Phase 6 (skills) is next

## Session Metadata
- Created: 2026-05-10 17:29:27
- Project: /Volumes/giuseppeM1mini-External/Coding/ubongo
- Branch: main (4d97300, Phase 5 merged via fast-forward)
- Session duration: ~1.5 days, multiple sessions stitched together
- User: Giuseppe Turitto

### Recent Commits (for context)

- 4d97300 Phase 5: mark complete in STATUS, populate smoke playbook
- 2b55f0e Phase 5b: events.unregister + after_send dispatch + vault handler registration
- b8f197f Phase 5a: vault writer + Obsidian-compatible daily note format
- b7880e4 a fix on the phase 4 plan (user-authored)
- 82ca365 Phase 4h+4i: update smoke playbook and STATUS for the memory robustness fixes
- 97e2b4d Phase 4i: cross-session summary inheritance + handler-registration fix
- c3b700b Phase 4h: cumulative summaries + lower compaction threshold
- f6da9e2 Phase 4: mark complete in STATUS, populate smoke playbook

## Handoff Chain

- **Continues from**: [2026-05-09-145144-phase-0-skeleton-complete.md](./2026-05-09-145144-phase-0-skeleton-complete.md)
- **Earliest in chain**: [2026-05-09-073800-ubongo-v01-spec-ready-for-phase-0.md](./2026-05-09-073800-ubongo-v01-spec-ready-for-phase-0.md)
- **Supersedes**: None (the prior handoffs stay valid for archival context)

> Read the previous handoff for Phase 0 cross-cutting decisions. This handoff documents Phases 1–5 in addition.

## Current State Summary

`main` is at `4d97300`. Phases 0–5 of the v0.1 build are merged: skeleton, REPL+one-shot, LiteLLM integration with persona registry and event bus, tone classifier with auto-routing and hysteresis, SQLite memory with cumulative compaction and cross-session summary inheritance, and Markdown vault projection. End-to-end: `uv run python -m ubongo` opens a REPL whose turns persist to `data/ubongo.db` and project to `vault/daily/<date>.md` as Obsidian-compatible notes; `/auto` routes via Qwen 2.5 7B classifier; conversations survive process restarts and the session-timeout boundary preserves durable facts via summary inheritance. Cumulative smoke for 0–5 ran as the last action and reported 34 PASS, 1 SKIP (manual Obsidian render), 1 BEHAVIOR-NOTE (3.3 hysteresis on "lol"). 75/75 pytest. The next agent should start by writing `Plans/phase-6-skills.md` based on UBONGO_BUILD.md's Phase 6 spec.

## Architecture Overview

The runtime is a single Python process with no async yet (asyncio lands in Phase 12). Per turn, the REPL/one-shot path:

1. Reads/creates a session row (`memory.store.current_or_new_conversation`) — single-user `user_id=1`, 30-min idle timeout via `settings.memory.session_timeout_minutes`.
2. If `auto_mode=True`, calls `classifier.classify(message)` → `router.route(classification)` → `router.apply_hysteresis(current, suggested, confidence)` reading the threshold from `settings.governance.confidence_threshold_for_auto`.
3. Appends user message, calls `recall(conv_id)` (which prepends summary into system prompt), invokes `llm.complete()`, appends assistant message.
4. `recall()` dispatches `after_recall`; `compaction._compaction_handler` subscribes and runs `maybe_compact()` if 15+ messages since last summary. Cumulative: each new summary folds the prior summary's content via the strategy's `(prior_summary, new_messages) -> str` signature.
5. On successful turn, `repl.handle_text` dispatches `after_send`; `vault._after_send_handler` subscribes and appends an entry to `vault/daily/<date>.md`.
6. Cross-session: when a fresh conversation has no summary, `recall()` falls back to `latest_summary_from_other_conversations()` so durable facts (birthday, project name) survive the timeout.

Key seam: events.py is a synchronous registry. Every later phase plugs handlers into events that already dispatch with forward-compatible payloads. The Master Agent (Phase 8) will wrap the per-turn flow but the substrate stays the same.

## Critical Files

| File | Purpose | Phase |
|------|---------|-------|
| [src/ubongo/__main__.py](../../src/ubongo/__main__.py) | argparse entry; routes to REPL or one-shot | 0e/1e |
| [src/ubongo/config.py](../../src/ubongo/config.py) | settings.yaml load + ${VAR} resolution + ConfigError | 0b |
| [src/ubongo/context.py](../../src/ubongo/context.py) | `build_system_prompt(persona, skill, agent_role)` hierarchical loader; skill/agent_role still unreachable until Phase 6/8 | 0c |
| [src/ubongo/logging.py](../../src/ubongo/logging.py) | JSON formatter, whitelist redaction in `_redact` | 0d |
| [src/ubongo/repl.py](../../src/ubongo/repl.py) | REPL loop, slash dispatch, handle_text wires classifier+router+memory+events | 1, 3, 4, 5 |
| [src/ubongo/oneshot.py](../../src/ubongo/oneshot.py) | One-shot CLI, returns rc 1 on terminal LLM error | 1, 2 |
| [src/ubongo/llm.py](../../src/ubongo/llm.py) | LiteLLM wrapper, single retry, before_llm/after_llm events, LiteLLM noise silenced | 2 |
| [src/ubongo/classifier.py](../../src/ubongo/classifier.py) | Defensive Classification with whitelist vocab; before_classify/after_classify events | 3 |
| [src/ubongo/router.py](../../src/ubongo/router.py) | First-match-wins routing.yaml + hysteresis from settings; `_WORKFLOW_TO_PERSONA` is a Phase-3 shortcut Phase 8 replaces | 3 |
| [src/ubongo/events.py](../../src/ubongo/events.py) | Sync dispatcher; register/unregister/dispatch/clear | 2, 5 |
| [src/ubongo/agents/personas.py](../../src/ubongo/agents/personas.py) | Persona registry; default_model is a key into settings.yaml's models map | 2 |
| [src/ubongo/memory/__init__.py](../../src/ubongo/memory/__init__.py) | Imports `compaction` and `vault` so their handler registrations run | 4f, 4i, 5b |
| [src/ubongo/memory/store.py](../../src/ubongo/memory/store.py) | Conversation/Message/Summary/Session/RecallContext APIs; recall has cross-session fallback; UBONGO_FAKE_NOW for testing | 4 |
| [src/ubongo/memory/compaction.py](../../src/ubongo/memory/compaction.py) | Strategy registry, default cumulative summarizer, after_recall handler | 4d, 4e, 4h |
| [src/ubongo/memory/vault.py](../../src/ubongo/memory/vault.py) | append_to_daily_note + after_send handler | 5a, 5b |
| [src/ubongo/memory/schema.sql](../../src/ubongo/memory/schema.sql) | Full v0.1 schema; sessions has Phase-4 added auto_mode column not in original spec | 4a |
| [config/settings.yaml](../../config/settings.yaml) | All v0.1 config; trigger_at_turns=15 (was 30 in spec; lowered in 4h) | 0b, 4h |
| [config/routing.yaml](../../config/routing.yaml) | Spec verbatim | 3b |
| [config/personas/](../../config/personas/) | Persona body + frontmatter (default_model + max_tokens) | 0b, 2a |
| [config/UBONGO.md](../../config/UBONGO.md) | Global identity / conventions; loaded into every system prompt | 0b |
| [tests/manual/smoke_test.md](../../tests/manual/smoke_test.md) | Cumulative manual playbook (Phases 0–5 populated; 6–21 stubs) | 1, 2, 3, 4, 5 |
| [STATUS.md](../../STATUS.md) | Phase tracker; LOC counter (1648/15000) | every phase |

## Key Patterns Discovered

- **Workflow rule (memory: feedback_branch_per_phase.md):** branch per phase `phase-N-<name>`, user merges to main, agent does NOT auto-merge unless explicitly told to. Each merge is a fast-forward; specstory's `.specstory/statistics.json` keeps showing as modified during the session and gets stashed/popped around merges.
- **Plan-first rule (memory: feedback_plan_approval_explicit.md):** before code, write `Plans/phase-N-<name>.md`, ask any open questions via AskUserQuestion, then **wait for explicit document approval** before implementing. Open-question answers are not a green light.
- **Commit-per-sub-phase rule:** the user wants a separate commit per sub-phase on the branch, plus a final STATUS+smoke commit.
- **Cumulative-smoke-after-merge rule:** after merging a phase to main, run all the manual smoke tests for Phases 0 through the latest. Past two cumulative runs ran 5–8 minutes with ~50–80 LLM calls.
- **Logging contract:** stderr is JSON-only. LiteLLM and httpx loggers are pinned to WARNING in llm.py to keep this clean. Never write a logger that emits to stdout.
- **Persona system-prompt prepend:** the conversation summary is prepended to the system prompt as `## Conversation summary so far`, not injected as a leading user message. Keeps the message list pure conversation.
- **Cumulative summaries:** the strategy signature is `(prior_summary: str | None, new_messages) -> str`; the latest summary always covers from message 1 forward. `latest_summary()` returns the most-recent summary by `covers_to_message_id`.

## Tasks Finished

- [x] Plans/ docs for phases 0, 1, 2, 3, 4, 5 (all approved)
- [x] Phase 0 (Skeleton) — merged
- [x] Phase 1 (CLI REPL + one-shot, echo) — merged; bracket-echo replaced by LLM in Phase 2
- [x] Phase 2 (LLM integration via LiteLLM/OpenRouter, persona registry, event bus) — merged
- [x] Phase 3 (Tone classifier + auto-routing with hysteresis) — merged
- [x] Phase 4 (SQLite memory, conversation persistence, cumulative compaction, cross-session summary inheritance, session timeout) — merged. Includes the user-reported birthday bug fix (4h cumulative + 4i cross-session).
- [x] Phase 5 (Markdown vault projection, Obsidian-compatible daily notes, events.unregister) — merged
- [x] Cumulative smoke 0–5: 34 PASS, 1 SKIP (manual Obsidian), 1 BEHAVIOR-NOTE (3.3)
- [x] Memory saved: feedback_plan_approval_explicit.md (workflow correction)

## Files Modified

The diff against `main` from when this chain started is roughly:
- New: `src/ubongo/{__init__,__main__,config,context,logging,repl,oneshot,llm,classifier,router,events}.py`, `src/ubongo/agents/{__init__,personas}.py`, `src/ubongo/memory/{__init__,store,compaction,vault}.py`, `src/ubongo/memory/schema.sql`
- New: `config/{UBONGO.md,settings.yaml,routing.yaml,personas/{architect,operator,casual}.md}`
- New: `tests/{__init__,conftest}.py`, `tests/test_{repl,personas,events,classifier,router,memory_store,memory_compaction,vault}.py`
- New: `tests/manual/smoke_test.md` (populated for phases 0–5)
- New: `Plans/phase-{0-skeleton,1-cli-echo,2-llm,3-classifier,4-memory,5-vault}.md`
- New: `pyproject.toml`, `uv.lock`, `.env.example`, `.env` (gitignored), `vault/.gitkeep`
- Modified: `STATUS.md` (phase tracker + LOC), `.gitignore` (vault/* + !vault/.gitkeep)
- Generated at runtime (gitignored): `data/ubongo.db`, `vault/daily/*.md`

## Decisions Made

| Decision | Why | Where |
|----------|-----|-------|
| Python 3.11 floor | Matches README; gives match/StrEnum | pyproject.toml |
| hatchling build backend, src layout | uv default; zero-friction | pyproject.toml |
| Plain dict for config (no pydantic) | Validation is trivial in v0.1 | config.py |
| Custom JsonFormatter (no python-json-logger) | 20 LOC; keep deps minimal | logging.py |
| Persona frontmatter `default_model` indirects through settings.yaml | One place to swap models | personas.py + persona files |
| `auto_mode` persists in sessions table (not in original spec) | User asked for `/auto` to survive restart | schema.sql + repl.py |
| `trigger_at_turns: 15` (was 30 in spec) | Closes the dead-zone where facts vanish before compaction | settings.yaml + 4h |
| Cumulative summaries: Strategy = (prior, new_msgs) -> str | Without folding, multi-summary stacking orphans early facts (birthday bug) | compaction.py |
| Cross-session summary inheritance via `latest_summary_from_other_conversations()` | Birthday survives the 30-min timeout into a new conversation | store.py |
| Vault: YAML frontmatter + H1 + H2-per-turn; `(auto)` suffix on auto-routed | Obsidian-friendly, scannable | vault.py |
| `.gitignore` = `vault/*` + `!vault/.gitkeep` | Track marker, ignore generated dailies | .gitignore |
| Hardcoded `_WORKFLOW_TO_PERSONA` in router.py | Phase 8 replaces with workflows.yaml reader; not worth building yet | router.py |
| `_after_llm` no-op handler in memory/__init__.py | Seam exists for Phase 8 to extend; current writes are inline in handle_text | memory/__init__.py |
| Single user `user_id=1` everywhere | v0.1 is single-user (memory: feedback_ubongo_v0.1_full_vision.md) | store.py |
| LiteLLM noise silenced at module load in llm.py | stderr JSON-only invariant | llm.py |
| `UBONGO_FAKE_NOW` env var override | Test session timeout without sleeping 31 minutes | store.py |
| Don't auto-merge | Memory: feedback_branch_per_phase.md; user merges or explicitly authorizes | every phase end |

## Immediate Next Steps

1. **Wait for the user to ask for Phase 6.** Don't start without explicit instruction.
2. **When asked, cut `phase-6-skills` from main** (`git checkout -b phase-6-skills`).
3. **Read [UBONGO_BUILD.md](../../UBONGO_BUILD.md) lines 841+ for Phase 6 spec.** Goal: skills as folders with frontmatter + body; descriptions load at startup; bodies on activation; v0.1 ships `summarize-conversation` skill.
4. **Write `Plans/phase-6-skills.md` mirroring the prior phase plans:** Goal, sub-phases, files touched, testing plan, smoke updates, scope-out, open questions, definition of done.
5. **Ask open questions via `AskUserQuestion`. Then STOP.** Do NOT write code on the trigger of question answers — wait for the user to say "approved" / "looks good" / "proceed".
6. **After approval: implement sub-phase by sub-phase, one commit each.** Match the Phase 0–5 commit cadence.
7. **After branch merge: run cumulative smoke for Phases 0–6.** ~75–80 LLM calls; budget 6–10 minutes.

## Blockers / Open Questions

- [ ] **3.3 hysteresis behavior:** Qwen 2.5 7B classifies "lol" with confidence ~0.9 as casual; current hysteresis (≥0.7 threshold from `settings.governance.confidence_threshold_for_auto`) correctly switches. Spec test 3 expected the persona to stay architect. Options for the user to choose later: (a) accept, (b) raise threshold to 0.95, (c) implement consecutive-turn hysteresis. Not blocking Phase 6.
- [ ] **The `_after_llm` payload** is narrow (model/tokens only). Phase 8 will widen it so memory writes can move from inline to a single subscriber. Don't widen prematurely.

## Deferred Items

- Skill body activation (Phase 6 ships this — context.py's skill branch wakes up).
- Master Agent + workflow runner + agents/{research,coding,evaluator,repair,memory,critic,execution}.py (Phase 8+).
- workflows.yaml reader (Phase 8 replaces `_WORKFLOW_TO_PERSONA`).
- Governance, risk scoring, approval gates (Phase 14–15).
- Variant generation, fitness, GP loop (Phase 16–18).
- Embeddings + sqlite-vec (Phase 20).
- Bidirectional vault sync (Phase 21).

## Important Context

**Workflow rules a fresh agent MUST follow before doing anything:**

1. **Branch per phase.** `phase-N-<short-name>`. Cut from main. Never commit to main from a phase in progress.
2. **User merges, not the agent.** Memory: `feedback_branch_per_phase.md`. Wait for explicit "merge into main" instruction even when the user authorized prior merges.
3. **Plan first, code only after explicit approval.** Memory: `feedback_plan_approval_explicit.md`. Write `Plans/phase-N-<name>.md`, present it, **wait** for "looks good" / "approved" / "proceed". Open-question answers are NOT a green light.
4. **Commit per sub-phase.** Each sub-phase = one commit on the branch. Final commit updates STATUS.md + smoke playbook.
5. **Cumulative smoke after merge.** When the user says merge, fast-forward into main, then run smoke for Phases 0 through the latest.
6. **Communication style.** Direct prose, no hedging, no em-dashes, no emojis (unless user uses them first), minimal markdown in conversational replies. Codified in CLAUDE.md and config/UBONGO.md.
7. **TodoWrite reminders are noise here.** They fire after almost every tool call. Ignore unless the task genuinely benefits and never tell the user about the reminder.

**Live behavior verification you can run** (also in the smoke playbook):

- Birthday survival within session (4.4): tell Ubongo a fact in turn 1, run 15+ filler turns, ask the fact back. Should answer correctly.
- Cross-session inheritance (4.5): same as above, then `UBONGO_FAKE_NOW=<31 min later>` and ask in a new conversation. Should still answer.
- Vault writes (5.1): three turns, then check `vault/daily/<today>.md` has frontmatter + 3 H2 entries.
- Handler disable (5.3): `events.unregister("after_send", vault._after_send_handler)` then send a turn — vault file shouldn't grow but `messages` table should.

**Security notes:**
- The user rotated their OpenRouter API key once during this chain. The current key in `.env` is functional. `.env` is gitignored; `_redact` in logging.py whitelists what gets logged so the key value never lands in logs.
- A test exposed the key publicly once via `OPENROUTER_API_KEY=...` inline in chat. The user rotated. If you see another exposure, recommend rotation immediately.

## Assumptions Made

- The user is on the latest commit (`4d97300` as of this handoff). Run `git log -1` to verify.
- `.env` exists with a valid `OPENROUTER_API_KEY`. If not, Phase 0.2 smoke test will fail clearly.
- The user has Obsidian installed for the manual eyes-on render check (5.2). Skip that step in scripted runs.
- Single user, single machine, local-first. No multi-user, no remote sync.
- Network access for OpenRouter is available. Tests that need the network: smoke 2.1–2.4, 3.1–3.4, 4.1, 4.4, 4.5. Pytest mocks the LLM and runs offline.

## Potential Gotchas

- **`.specstory/statistics.json` modified during sessions.** It's editor metadata, not yours. Stash before checkouts/merges, pop after. Never commit it.
- **`data/ubongo.db` and `vault/daily/*.md` accumulate from runs.** When iterating, `rm -f data/ubongo.db vault/daily/*.md` between tests for clean state.
- **`compaction` and `vault` modules MUST be imported by `memory/__init__.py`** for their `events.register(...)` calls at module level to actually run. If you reorganize imports, verify the after_recall and after_send handlers still register on package load.
- **The compaction summary haiku model output sometimes prepends bold-Summary markdown.** Doesn't break anything but adds slight visual noise when the summary lands in the system prompt. Could be tightened by the compaction system prompt later.
- **Trigger threshold is 15 not 30.** Phase 4h changed `settings.memory.compaction.trigger_at_turns` from the spec's 30 to 15 to fix the dead-zone bug. If you read the spec it'll say 30; the live config is 15.
- **`_WORKFLOW_TO_PERSONA` in router.py is a Phase-3 shortcut.** Phase 8 will replace it with a workflows.yaml reader. Don't add to it casually.
- **`auto_mode` persists across REPL restart.** A fresh agent starting the REPL might be surprised the loop is in auto mode if a prior session set it.
- **Compaction LLM calls happen synchronously every ~5–6 messages once past the threshold.** Adds ~1s latency to those turns. Phase 13's Repair Agent or a background task could move it off the hot path.
- **The `summary_inherited` flag in `after_recall` payloads** tells subscribers if the summary came from a prior conversation. New handlers should respect it.
- **`UBONGO_FAKE_NOW` is read every call to `store._now()`.** Set it in a single command, don't expect it to persist across processes unless you export it.

## Tools / Services Used

- `uv` 0.8.22 at `/Library/Frameworks/Python.framework/Versions/3.13/bin/uv`
- Python 3.13 in the venv (pyproject `requires-python = ">=3.11"`)
- `git` on `main` (4d97300); origin/main is 3 behind, the user can `git push` when convenient
- `sqlite3` for DB inspection
- `jq` for stderr JSON parsing in smoke tests
- OpenRouter (sonnet-4.5 default, haiku-4.5 casual+compaction, qwen 2.5 7B classifier)

## Active Processes

- None. Single-shot CLI; nothing daemonized.

## Environment Variables

- `OPENROUTER_API_KEY` — set in `.env`, validated by `load_config`. Don't echo or log.
- `UBONGO_FAKE_NOW` — optional ISO 8601 string; overrides `_now()` in store.py for tests and smoke scenario 5.4.
- Phase 0e mentioned `TELEGRAM_BOT_TOKEN`, Google/Gmail/Reddit client placeholders in `.env.example` — all empty, unused until v0.2+.

## Related Resources

- `UBONGO_BUILD.md` — 22-phase spec; lines 841+ are Phase 6
- `STATUS.md` — phase tracker (Phases 0–5 marked Complete)
- `tests/manual/smoke_test.md` — cumulative playbook through Phase 5
- `Plans/` — six phase plans (0–5) committed, with the user-authored "a fix on the phase 4 plan" commit on top of mine
- `CLAUDE.md` — project rules
- Memory: `feedback_branch_per_phase.md`, `feedback_plans_folder.md`, `feedback_plan_approval_explicit.md`, `feedback_ubongo_v0.1_full_vision.md`, `feedback_ubongo_cli_first.md`

---

**Security Reminder**: Before finalizing, run `validate_handoff.py` to check for accidental secret exposure.
