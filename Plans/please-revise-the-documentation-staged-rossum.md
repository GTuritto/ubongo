# Documentation Revision — Ubongo (CLI-first pivot)

## Progress So Far (re-entered plan mode mid-execution)

Most of the approved plan is already on disk. Captured here so the remaining work is unambiguous.

**Done:**

- `Ubongo_PRD.md` removed via `git rm` (was a byte-for-byte duplicate of `Ubongo.md`).
- `Ubongo.md` renamed to `UBONGO_VISION.md` via `git mv`; disclaimer block + `# Ubongo — Vision (Origin Document)` heading prepended.
- `README.md` fully rewritten for the CLI pivot (REPL + one-shot, no Telegram, pre-implementation status, Documentation Map, updated Roadmap).
- `CLAUDE.md` created at repo root.
- `STATUS.md` created with phase tracker, LOC budget, and acceptance-criteria checklist.
- `.gitignore` created.
- `Plans/` directory created at repo root.
- Memory files saved: `feedback_ubongo_cli_first.md`, `feedback_plans_folder.md`, plus `MEMORY.md` index.
- `UBONGO_BUILD.md` edits applied: "What Ubongo Is", "What Ubongo Is Not", Core Design Decisions #1/#9/#10/#14, Tech Stack table (dropped python-telegram-bot row), Architecture diagram, File Structure, `before_send` event-handler rows, SQL schema (dropped `delivery_policy` table + index), entire "Notification Queue and Delivery Policy" section, `.env.example`, `settings.yaml`, Phase 1, Phase 7, Acceptance Criteria, Out of Scope, v0.2 Sketch (CLI front-end → Telegram channel), Setup Instructions, Final Notes for Claude Code.

**Remaining work to finish on resume:**

1. Update the small "## CLAUDE.md (for future Claude Code sessions)" section *inside* `UBONGO_BUILD.md` (it currently says the project will ship CLAUDE.md — now CLAUDE.md actually exists, so the wording can be tightened to point at the real file).
2. Copy the finalized plan from `~/.claude/plans/please-revise-the-documentation-staged-rossum.md` into the project's `Plans/` directory so it's readable from the project itself.
3. Run the verification checks listed in the **Verification** section below: `ls -A` of project root, `grep` checks for stale Telegram/PRD references, sanity-read `UBONGO_VISION.md` and `CLAUDE.md`.
4. (Skipped — repo is now a git repo, but no commit was requested. Don't auto-commit unless the user asks.)

There is no scope change. Re-enter execution and finish the four items above.

## Context

Ubongo is a personal, mood-aware AI assistant for a single user (Giuseppe). Today's documentation describes a **Telegram-first** v0.1 build with a notification queue, policy engine, quiet hours, and holds. **You've decided to pivot v0.1 to a local CLI** — REPL primary, one-shot `ubongo send "..."` for scripting — and to defer Telegram and the full queue/policy machinery until the core system works as desired in CLI form.

This means the revision is no longer just a doc cleanup. The build spec has Telegram baked into the architecture, Phase 1, configuration, slash commands, and 6 of 19 acceptance criteria. To make the docs coherent we need to **re-spec v0.1**, then layer the structural cleanup (duplicate file, missing CLAUDE.md, missing STATUS.md, vision-doc disclaimer) on top.

User decisions in this round:
- **CLI form**: both — REPL primary (`uv run python -m ubongo`) plus one-shot (`ubongo send "..."`)
- **Telegram fate**: removed from v0.1 docs entirely; revisit when v0.1 works
- **Queue**: minimal SQLite-backed outbound queue stays (so future scheduled jobs have somewhere to write); policy engine, quiet hours, holds, catch-up summarizer all defer

## Files Affected

| Path | Action |
| --- | --- |
| [UBONGO_BUILD.md](UBONGO_BUILD.md) | **Major rewrite** — re-spec v0.1 around CLI; strip Telegram-specific design; simplify queue |
| [README.md](README.md) | **Rewrite Setup/Run/Usage** for CLI; trim Telegram references; mark pre-implementation status |
| [Ubongo.md](Ubongo.md) | Rename → `UBONGO_VISION.md`; prepend disclaimer |
| [Ubongo_PRD.md](Ubongo_PRD.md) | Delete (byte-for-byte duplicate of `Ubongo.md`) |
| `CLAUDE.md` | Create — derived from `UBONGO_BUILD.md`'s spec, updated for CLI pivot |
| `STATUS.md` | Create — phase tracker, all phases not started |
| `.gitignore` | Create — covers secrets, SQLite, vault, Python build artifacts, IDE/Claude state |
| `Plans/please-revise-the-documentation-staged-rossum.md` | Create at end — copy of this plan into a project-local `Plans/` folder so future sessions can read past plans |

Repo is **not** a git repo per environment context, so `rm` and `mv` are fine; no `git rm` / `git mv`. The `.gitignore` is added now in anticipation of `git init` being run later; it's harmless until then.

## Core Spec Changes (v0.1 → CLI)

These ripple through every file. Summarizing them once here so the per-file edits below stay short.

**Channel.** Telegram → CLI with two surfaces:
- REPL: `uv run python -m ubongo` opens an interactive loop. Each input is one turn through the router/persona/memory pipeline.
- One-shot: `uv run python -m ubongo send "<message>" [--persona <name>] [--skill <name>]` runs a single turn and exits. Shares SQLite state with the REPL.
- Eventual `ubongo` console-script entry point in `pyproject.toml` so `ubongo` and `ubongo send` work without `python -m`.

**Slash commands** (REPL only; one-shot uses CLI flags):
- Keep: `/architect`, `/operator`, `/casual`, `/auto`, `/summary`, `/skills`, `/reload`
- Add: `/exit` (or `/quit`) for REPL termination
- Add: `/queue` (read-only) — show current contents of the minimal outbound queue
- **Drop entirely**: `/hold`, `/resume`, `/quiet` — no policy engine in v0.1

**Authentication.** Drop. There's no `allowed_user_ids` for a local CLI; the user is whoever runs the binary.

**Configuration changes:**
- `.env.example`: drop `TELEGRAM_BOT_TOKEN`. Keep `OPENROUTER_API_KEY`.
- `config/settings.yaml`: drop the entire `telegram:` block. Drop the `delivery.quiet_hours`, `delivery.hold_until_ack_warning_hours`, `delivery.catchup` sub-blocks. Keep `delivery.worker_poll_seconds` only if the minimal queue is implemented as a worker (otherwise drop the whole `delivery:` block; see queue note below).
- `config/urgency.yaml`: keep as empty stub (still v0.3+).

**Source layout changes** (`src/ubongo/`):
- Replace `bot.py` (Telegram handlers) with `repl.py` and `oneshot.py` (or `cli.py` containing both — implementation detail, leave to Phase 1).
- `delivery/`:
  - Keep: `queue.py` (minimal: enqueue, dequeue, mark_delivered)
  - **Drop**: `policy.py`, `worker.py` (asyncio worker for Telegram delivery), `catchup.py`, `commands.py`
  - Note: with no Telegram and no scheduler, the "worker" is conceptual only. Sync responses are written to the queue at `urgency=urgent`, then read out and printed to stdout in the same turn. No background asyncio task needed in v0.1. The queue exists so v0.3's scheduler has somewhere to put proactive output.

**Schema changes** (`memory/schema.sql`):
- Keep `notification_queue` table (the minimal queue still persists outbound items).
- **Drop** `delivery_policy` table — no policy engine.
- All other tables (`conversations`, `messages`, `summaries`, `sessions`, `facts`) unchanged.

**Event changes** (`events.py`):
- Keep all eight named events: `before_classify`, `after_classify`, `before_recall`, `after_recall`, `before_llm`, `after_llm`, `before_send`, `after_send`. The seam is still valuable.
- Default handlers in v0.1: compaction (`after_recall`), memory write (`after_llm`), vault projection (`after_send`). **Drop** the `policy_check` default handler on `before_send`; it becomes a passthrough until Telegram lands.

**Architecture diagram** (currently Telegram-centric in `UBONGO_BUILD.md`):
- `Telegram message` → `stdin (REPL) or argv (one-shot)`
- `delivery worker checks policy, sends to Telegram` → `enqueue + immediate dequeue → stdout`
- Everything in between (auth, classify, route, recall, prompt assembly, LLM, memory write) is unchanged.

## Per-File Changes

### 1. Delete `Ubongo_PRD.md`

Byte-for-byte duplicate of `Ubongo.md`. No content lost.

### 2. Rename `Ubongo.md` → `UBONGO_VISION.md`, prepend disclaimer

The body is the conceptual origin (Master Agent, LangGraph, GP, multi-agent orchestration). It does **not** match v0.1 reality and never will. Keep it as design history with a top-of-file block:

```markdown
> **This document is the conceptual origin of Ubongo, not the v0.1 specification.**
>
> It captures the broad architectural exploration — Master Agent orchestration, parallel agents, governance layers, Genetic Programming — that shaped the project's intent. The actual v0.1 build deliberately scopes most of this *out*: it's a single-user **CLI** assistant with a stateless router (no Master Agent), three personas, and no agent lifecycle. Telegram is deferred until after v0.1 works as desired.
>
> For the v0.1 scope, build phases, and acceptance criteria, see **[UBONGO_BUILD.md](UBONGO_BUILD.md)**.
> For setup and current status, see **[README.md](README.md)** and **[STATUS.md](STATUS.md)**.
```

Body unchanged.

### 3. Rewrite parts of `UBONGO_BUILD.md`

The largest piece of work. Changes by section, with line numbers from the current file:

| Lines | Section | Change |
|---|---|---|
| 9 | "What Ubongo Is" | Rewrite: "lives in Telegram" → "runs as a local CLI (REPL plus one-shot)". Drop "Outbound messages flow through a notification queue with a policy engine that respects quiet hours and ad-hoc holds" — replace with "A minimal outbound queue is in place so future proactive jobs have a delivery path". |
| 11–19 | "What Ubongo Is Not (v0.1)" | Add to deferred list: Telegram (and any other external channel), notification policy engine, quiet hours, holds, catch-up summarizer. |
| 23 | Core Design Decision #1 | "Single channel: Telegram" → "Single channel: CLI. Telegram is the planned second channel after v0.1 works." |
| 31–32 | Core Design Decisions #9–#10 | Reduce: "All outbound messages flow through a notification queue" stays; "Policy engine governs delivery" — soften to "A policy engine seam is reserved for when Telegram lands; v0.1 has no policy logic." |
| 36 | Core Design Decision #14 | Replace "One user. Hardcode the allowed Telegram user ID..." with "One user. The CLI runs locally; there is no auth boundary in v0.1." |
| 43 | Tech Stack table | Remove `python-telegram-bot` row from v0.1 stack. Note in prose: "python-telegram-bot will be added when the Telegram channel ships post-v0.1." |
| 56–85 | Architecture diagram | Rewrite per "Architecture diagram" bullet above. |
| 89–152 | File Structure | Replace `bot.py` with `repl.py` + `oneshot.py` (or `cli.py`). Trim `delivery/` to just `__init__.py` + `queue.py`. Update `tests/` listing accordingly. |
| 295 (event table) | `before_send` default handler | Change "policy engine (decides deliver/hold)" → "passthrough (policy engine deferred to post-v0.1)". |
| 405–431 | SQL schema | Drop `delivery_policy` table entirely. Keep `notification_queue` table. Drop `idx_policy_active` index. |
| 465–503 | "Notification Queue and Delivery Policy" | Massive rewrite. Drop quiet-hours discussion, holds, hold-until-ack, catch-up, slash commands `/hold` `/resume` `/quiet`, natural-language detection, worker loop wakeup-on-urgent. Replace with a short section: "Minimal outbound queue. Every response gets enqueued at `urgency=urgent` then immediately dequeued and printed in the same turn. The queue persists in SQLite so post-v0.1 scheduled jobs have somewhere to write proactive output. No policy engine in v0.1; the `before_send` event is a passthrough." |
| 519–533 | `.env.example` | Drop `TELEGRAM_BOT_TOKEN`. |
| 537–580 | `settings.yaml` | Drop `telegram:` block. Drop `delivery.quiet_hours`, `delivery.hold_until_ack_warning_hours`, `delivery.catchup`. May drop the entire `delivery:` block depending on whether the minimal queue needs config. |
| 592–601 | "CLAUDE.md (for future Claude Code sessions)" | Keep this section as the **source** for the new top-level `CLAUDE.md` file we'll create. Update wording to reflect CLI v0.1, no Telegram. |
| 625–636 | Phase 1 | Full rewrite: "Telegram Echo Bot with Persona Switching" → "CLI REPL with Persona Switching". REPL takes input, echoes back with current persona name, supports `/architect` `/operator` `/casual` `/auto` `/exit`. Add brief mention that one-shot mode comes alongside (`ubongo send "msg"`). Stub responses, no LLM yet. Acceptance: typing in REPL produces `[architect] hello` style echo; `/casual` switches persona; `/exit` quits cleanly. |
| 651–661 | Phase 3 | Mostly unchanged (channel-agnostic) — just remove any Telegram-specific phrasing. |
| 678–688 | Phase 5 | Unchanged — vault is channel-agnostic. |
| 690–702 | Phase 6 | Unchanged conceptually; just verify any examples don't assume Telegram. |
| 704–744 | Phase 7 | Massive simplification. Drop sub-steps 7b (policy), 7c (slash commands), 7d (catch-up), 7e (NL hold detection), 7f (hold-until-ack). Keep only sub-step 7a equivalent: implement the minimal SQLite queue, enqueue/dequeue, integrate into the response path. Add a `/queue` REPL command that reads the queue contents (debug/inspection). |
| 746–770 | Acceptance Criteria | Revise: <br>• #1 "responds to Telegram messages" → "responds in REPL and one-shot modes" <br>• #2–4 unchanged (persona behavior is channel-agnostic) <br>• #5–11 unchanged <br>• #12 unchanged ("every outbound message goes through the notification queue") <br>• **Drop #13** (quiet hours) <br>• **Drop #14** (hold/resume/quiet/queue commands — except keep `/queue` read-only) <br>• **Drop #15** (NL hold instructions) <br>• **Drop #16** (catch-up) <br>• #17 (events fire) unchanged <br>• #18 unchanged <br>• #19 (LOC budget) — consider lowering from 3000 to 2500 since several subsystems were cut. Suggest 2500. |
| 772–790 | Out of Scope (v0.1) | Add: Telegram and any external channel, notification policy engine, quiet hours, ad-hoc holds, hold-until-ack, catch-up summarizer, natural-language hold detection. |
| 794–804 | v0.2 Sketch | Replace "CLI front-end" entry with "Telegram channel — bring back `python-telegram-bot`, `allowed_user_ids` auth, and the policy engine + quiet hours + holds + catch-up that were specced for v0.1 and deferred. The queue and event seams from v0.1 mean this is mostly transport plus the policy handler on `before_send`." Other v0.2 entries (Calendar, embeddings, topic compaction, facts, vault sync, fourth persona) unchanged. |
| 855–878 | Setup Instructions | Drop Telegram-related setup. Replace with: install uv, `uv sync`, copy `.env.example` to `.env`, set `OPENROUTER_API_KEY`, optionally edit `config/UBONGO.md` and personas, run `uv run python -m ubongo`. |
| 882–892 | Final Notes for Claude Code | Update phase-1 reference and any Telegram-specific guidance. Reaffirm: when Telegram is added later, the queue/event seams should make it additive. |

### 4. Rewrite parts of `README.md`

Targeted edits, no full rewrite. Specific changes by line:

- **Lines 1–9 (intro + Status)**: rewrite the intro: "lives in Telegram" → "runs as a local CLI". Update Status to: "v0.1 specification complete; **no implementation yet**. The Setup/Run/Usage sections describe the *intended* v0.1 behavior. See [STATUS.md](STATUS.md) for current phase progress."
- **Lines 11–13 ("What Ubongo Is")**: drop "single-user Telegram bot" and "stores the conversation in SQLite with an Obsidian-compatible Markdown projection" stays. Replace with "single-user CLI assistant. Type to it (REPL) or pipe one-shot commands; it routes each turn to one of three personas (Architect, Operator, Casual), runs the appropriate model via OpenRouter, and stores the conversation in SQLite with an Obsidian-compatible Markdown projection."
- **Lines 15–19 ("What Ubongo Is Not")**: append: "Not Telegram-first in v0.1; Telegram is the planned second channel once v0.1 works as desired."
- **Lines 21–31 (Tech Stack)**: drop the `python-telegram-bot` line. Note "(Telegram client added in v0.2)".
- **Lines 33–39 (Prerequisites)**: drop the Telegram bot token bullet and the Telegram numeric ID bullet. Keep Python, uv, OpenRouter.
- **Lines 41–72 (Setup)**: drop `TELEGRAM_BOT_TOKEN` from the `.env` example. Drop the `telegram.allowed_user_ids` settings.yaml block. Keep the `OPENROUTER_API_KEY` and the `config/UBONGO.md` / persona editing notes.
- **Lines 73–86 (Run)**: rewrite. Show both modes:
  ```bash
  # REPL (interactive)
  uv run python -m ubongo

  # One-shot
  uv run python -m ubongo send "draft a migration plan"
  uv run python -m ubongo send --persona casual "what should I cook tonight"
  ```
  Drop the "send it a message from your allowed account" text. Drop the polling-related troubleshooting.
- **Lines 87–105 (Usage)**: keep slash commands `/architect`, `/operator`, `/casual`, `/auto`, `/summary`, `/skills`, `/reload`. **Drop**: `/hold 3h`, `/hold until 18:00`, `/hold`, `/resume`, `/queue`, `/quiet`. Add `/exit` for REPL exit. Optionally keep `/queue` as a read-only inspection command. Drop the "Quiet hours and holds only affect *proactive* messages" paragraph.
- **Lines 107–119 (Configuration)**: drop the `urgency.yaml` row or label it "v0.3+". Drop or simplify the `delivery` policy references. The rest stands.
- **Lines 127–150 (Project Structure)**: prepend a one-line "Planned layout (does not yet exist on disk):". Replace `bot.py` with `repl.py` + `oneshot.py` (or `cli.py`). Trim `delivery/` to just `queue.py`. Drop `commands.py`, `policy.py`, `worker.py`, `catchup.py`.
- **Lines 154–162 (Roadmap)**: rewrite:
  - **v0.1 (current target)**: CLI (REPL + one-shot), three personas, tone routing, SQLite memory, Markdown vault, skills with progressive disclosure (one demo skill), minimal outbound queue.
  - **v0.2**: Telegram channel (with the deferred policy engine, quiet hours, holds, catch-up). Pick one or two from: Google Calendar integration, embedding-based recall, topic-aware compaction, structured fact extraction, bidirectional vault sync, fourth persona.
  - **v0.3**: Scheduler for proactive jobs. Additional integrations as skills (email, news, Reddit), each invoked via a `bash` tool against a CLI script.
- **Add Documentation Map section** before `## Configuration`:
  ```markdown
  ## Documentation Map
  - [README.md](README.md) — this file. Goal, setup, usage, roadmap.
  - [UBONGO_BUILD.md](UBONGO_BUILD.md) — full v0.1 build specification, phased plan, acceptance criteria. Source of truth.
  - [UBONGO_VISION.md](UBONGO_VISION.md) — origin / inspiration document. Conceptual exploration. Not the v0.1 spec.
  - [CLAUDE.md](CLAUDE.md) — context for Claude Code sessions working on this repo.
  - [STATUS.md](STATUS.md) — current implementation phase and progress.
  ```

### 5. Create `CLAUDE.md`

Source: the "CLAUDE.md (for future Claude Code sessions)" section of `UBONGO_BUILD.md` (lines 592–601), updated to reflect the CLI pivot. Contents:

- One-paragraph project description: "Ubongo is a personal, mood-aware CLI assistant for a single user (Giuseppe). v0.1 is local-only — REPL plus one-shot. Telegram is the planned second channel after v0.1 ships."
- "What Ubongo Is Not (v0.1)" verbatim from updated `UBONGO_BUILD.md`, including the Telegram deferral.
- Current phase status: pointer to `STATUS.md`.
- Pointer to `UBONGO_BUILD.md` as the build spec source of truth.
- Convention notes: prose over bullets, no em-dashes, no emojis, direct tone (Giuseppe's preferences, also in `config/UBONGO.md`).
- Architectural rules: every outbound message goes through the queue; secrets only in `.env`; new capabilities ship as skills not as REPL/CLI modifications; new behavior ships as event handlers; new tools default to CLI scripts; no Telegram-specific code in v0.1.

### 6. Create `STATUS.md`

Short, living document. Initial contents:

- Header: "Last updated: 2026-05-09"
- Phase tracker table (Phase 0 through Phase 7), all rows "not started" with target deliverable in one line each.
- Lines-of-code: 0 / target 2500 (or whatever LOC budget the build-spec acceptance criteria settles on).
- Acceptance criteria checklist (all 13–14 v0.1 criteria after the Telegram-related drops), all unchecked.
- Note: "Update this file as phases land."

### 7. Create `.gitignore`

The build spec calls for a `.gitignore` covering at least `/vault`, `*.db`, and `.env`. Adding it now (even though the repo isn't yet `git init`-ed) so that the moment it becomes a git repo, secrets and generated artifacts are already excluded. Proposed contents:

```gitignore
# Secrets
.env
.env.*
!.env.example

# SQLite (generated by the app)
*.db
*.db-journal
*.db-wal
*.db-shm

# Generated vault notes
vault/

# Python
__pycache__/
*.py[cod]
*$py.class
*.egg-info/
dist/
build/
.venv/
venv/
env/

# Test / lint caches
.pytest_cache/
.ruff_cache/
.mypy_cache/
.coverage
htmlcov/

# Claude Code / editor state
.specstory/
.history/

# OS
.DS_Store
Thumbs.db

# Logs
*.log
```

Notes:
- `.vscode/` is **not** included — leaving the user's editor config un-ignored unless they say otherwise. If they want it ignored, add it later.
- `Plans/` is intentionally **not** ignored: the user wants past plans committed and readable.

### 8. Create `Plans/` folder and copy this plan

After all other revisions land, create `/Volumes/giuseppeM1mini-External/Coding/ubongo/Plans/` and copy this plan file (`please-revise-the-documentation-staged-rossum.md`) into it. Going forward, every plan we write together is duplicated into the project's `Plans/` folder so it's readable later from the project itself, not just from `~/.claude/plans/`.

## Out of Scope (Deferred)

These were observed but are *not* part of this revision:

- Filling in spec gaps the Explore agent flagged (skill trigger precedence between command/classifier/manual, session-timeout edge cases at 29:59 vs 30:01, error-handling strategy for OpenRouter rate limits, security model for API keys). These are real gaps but belong in a future spec-tightening pass, not this CLI pivot.
- Verifying model names against the live OpenRouter catalog (e.g., `openrouter/anthropic/claude-sonnet-4.5` referenced in the build spec). Recommend verifying at Phase 2 implementation, not now.
- Converting `UBONGO_VISION.md` into a formal PRD with user stories. User chose to preserve as vision.

## Verification

After changes:

1. `ls -A /Volumes/giuseppeM1mini-External/Coding/ubongo/` shows: `README.md`, `UBONGO_BUILD.md`, `UBONGO_VISION.md`, `CLAUDE.md`, `STATUS.md`, `.gitignore`, `Plans/`. **Not** `Ubongo.md` or `Ubongo_PRD.md`. (`.vscode/`, `.specstory/`, `.history/` may still be present — they're not deleted, just gitignored.)
2. `grep -nri "telegram" /Volumes/giuseppeM1mini-External/Coding/ubongo/*.md` returns hits only in: (a) v0.2 sketch sections describing the deferred channel, (b) explicit "deferred / out of scope" lines, (c) the vision doc (which is allowed to mention it as origin context). No live setup/run/usage instructions reference Telegram.
3. `grep -nri "/hold\|/resume\|/quiet" /Volumes/giuseppeM1mini-External/Coding/ubongo/*.md` returns nothing in the README's Usage section or the build spec's Phase 7. (May still appear in v0.2 sketch.)
4. `grep -nri "Ubongo\.md\|Ubongo_PRD\.md" /Volumes/giuseppeM1mini-External/Coding/ubongo/*.md` returns nothing — no stale links.
5. `grep -ri "UBONGO_VISION" /Volumes/giuseppeM1mini-External/Coding/ubongo/*.md` returns hits in at least `README.md`, `UBONGO_BUILD.md`, `CLAUDE.md` — the rename is properly cross-referenced.
6. Reading `README.md` end-to-end as a fresh contributor: it should be obvious that (a) v0.1 is a CLI, (b) Telegram comes later, (c) no code exists yet, (d) the build spec is `UBONGO_BUILD.md`.
7. Reading `UBONGO_VISION.md`: the first block makes it clear this is design history, not the spec.
8. `CLAUDE.md` exists; a fresh Claude Code session has enough context to know: CLI v0.1, no code yet, where the spec lives, what's deferred.
9. `.gitignore` exists at the repo root and includes at minimum `.env`, `*.db`, `vault/`. `cat .gitignore | grep -E '^\.env$|^\*\.db$|^vault/$'` returns three lines.
10. `Plans/please-revise-the-documentation-staged-rossum.md` exists and matches the contents of the home-directory plan file.

No tests to run — documentation only. No code is touched.

## Memory to Save (after plan approval, outside plan mode)

Two pieces of durable user feedback to persist for future sessions:

1. **Ubongo: build CLI-first, defer Telegram.** User wants to validate the router/persona/memory/skill behavior in isolation before introducing the latency, asynchrony, and external-service complexity of Telegram. Stated explicitly: "We will implement the Telegram once we are done and it works as we want." Apply to all future Ubongo work: don't propose Telegram features as part of v0.1, treat the CLI as the v0.1 channel, the queue/event seams should make Telegram additive when it lands.

2. **Plans go in a project-local `Plans/` folder.** Every time a plan is created in plan mode, also save a copy to the project's `Plans/` directory so the user can read past plans from the project. The system's `~/.claude/plans/` location is fine for plan-mode operation, but isn't visible from the project. Apply to: every project where plan mode is used. The plan filename can match the system's filename.
