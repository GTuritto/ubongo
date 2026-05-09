# Handoff: Ubongo v0.1 spec complete; ready for Phase 0 implementation

## Session Metadata

- Created: 2026-05-09 07:38:00
- Project: /Volumes/giuseppeM1mini-External/Coding/ubongo
- Branch: main
- Session duration: ~3 hours of working time across two scope pivots
- User: Giuseppe Turitto

### Recent Commits (for context)

- 46e2bcf Initial commit

> Note: all documentation work in this session is **uncommitted on `main`**. The repo is git-init'd but no commit has been made for this session's changes yet. The user has not asked for a commit. Do not auto-commit.

## Handoff Chain

- **Continues from**: None (fresh start)
- **Supersedes**: None

> This is the first handoff for the Ubongo project. Two earlier plan files are archived in `Plans/`.

## Current State Summary

The project is **100% specification, 0% code**. v0.1 is now fully specified as a multi-agent orchestration platform plus a self-improving runtime, running locally as a CLI. The doc set is coherent and ready to be implemented; what remains is to execute the 22-phase build plan starting with Phase 0 (skeleton). Each phase must be built on a dedicated branch (`phase-N-<short-name>`) and merged into `main` only after the user approves the phase's testing plan and end-to-end smoke test. The next session should start by branching `phase-0-skeleton` off `main`.

This session went through two scope pivots that the docs now reflect:

1. **CLI-first pivot.** The original spec had Telegram as the v0.1 channel. User redirected to a local CLI (REPL primary, one-shot for scripting) with Telegram deferred to v0.2. The notification policy engine, quiet hours, and holds (designed around proactive Telegram delivery) were deferred to v0.2 with it.
2. **Multi-agent + self-improving redesign.** User then directed v0.1 to include the full vision from `UBONGO_VISION.md`: Master Agent + 8 worker types + all 6 execution modes + governance layer + continuous Genetic Programming loop with human-approved promotions. Hand-rolled Python (no LangGraph). LOC budget revised from 2500 to ~15,000 soft target.

## Architecture Overview

Hand-rolled Python multi-agent runtime, plain `asyncio` and an event bus. The core loop per CLI turn:

```text
classify (small fast model, JSON) → plan (consult routing.yaml + workflows.yaml)
→ execute (Workflow Runner spawns worker agents; one of six modes: sequential,
parallel, competitive, collaborative, debate, speculative)
→ govern (decision matrix: intent + risk + confidence + reversibility +
preferences + context → action ∈ {auto, ask_clarification, require_approval, reject})
→ compose (Persona Agent shapes user-facing text)
→ enqueue (notification_queue, urgency=urgent for sync responses)
→ send (dequeue + print to stdout, fire before_send/after_send events)
→ remember (Memory Agent writes SQLite + vault + embeddings)
```

A continuous Genetic Programming loop runs on a separate asyncio task: generates variants of evolvable targets (persona prompts, classifier prompt, routing rules, workflow templates, retry strategies); evaluates them in a sandbox against held-out conversation samples; persists lineage; queues winners in `pending_promotions` for user approval via `/improvements`. Nothing promotes without explicit `approve`.

Worker agents (full set, all v0.1):

- Research, Coding, Evaluator, Repair, Memory, Critic, Execution, Persona Agents (Architect / Operator / Casual).
- Each implements a common `Agent` protocol (`run(input, context) -> AgentResult`).
- Disposable: spawn per workflow, run, return, dissolve.
- Memory Agent is the only writer to durable state (SQLite, vault, embeddings). Other agents return findings; Memory Agent commits.

Memory layers: SQLite canonical (`workflow_runs`, `agent_runs`, `governance_decisions`, `evolution_lineage`, `evolution_evaluations`, `pending_promotions`, `active_evolutions`, `notification_queue`, `vault_links`, plus the existing `conversations`/`messages`/`summaries`/`sessions`/`facts` set). `sqlite-vec` virtual tables (`vec_messages`, `vec_vault`) for semantic recall. Markdown vault (`vault/daily/YYYY-MM-DD.md`) projected from SQLite, plus an audit log at `vault/system/audit.md`.

## Critical Files

| File | Purpose | Relevance |
| --- | --- | --- |
| [UBONGO_BUILD.md](../../UBONGO_BUILD.md) | Build spec: 22 phases with sub-phases, testing plans, smoke tests, branch names. Source of truth. | Read first before any implementation work. |
| [STATUS.md](../../STATUS.md) | Phase tracker with branch names; LOC budget; acceptance-criteria checklist. | Update as phases land. |
| [CLAUDE.md](../../CLAUDE.md) | Project-level context for Claude Code sessions. Conventions, architectural rules, branch workflow. | Auto-loaded by Claude Code; treat as authoritative for "how we work." |
| [README.md](../../README.md) | Human-facing entry point. How it works, slash commands, contributing workflow. | Keep in sync if scope or commands change. |
| [UBONGO_VISION.md](../../UBONGO_VISION.md) | Design exposition the v0.1 build realizes. Disclaimer at top points to `UBONGO_BUILD.md`. | Reference for the "why" behind the architecture. |
| [Plans/](../../Plans/) | Two archived plans: the CLI doc-revision plan and the multi-agent redesign plan. | Read when picking up the project to understand decisions. |
| [tests/manual/smoke_test.md](../../tests/manual/smoke_test.md) | Cumulative end-to-end manual playbook. Stub today; populated phase by phase. | Gets new sections appended at every phase end. |
| `~/.claude/projects/-Volumes-giuseppeM1mini-External-Coding-ubongo/memory/` | Four memory files capturing user preferences. | Auto-loaded; verify before assuming. |

## Key Patterns Discovered

- **Hand-rolled, not framework-led.** No LangGraph, no Temporal, no Ray. Plain Python with classes, `asyncio`, an event bus, and a workflow runner. The user explicitly chose this.
- **Branch per implementation phase.** `phase-N-<short-name>` (names listed in [UBONGO_BUILD.md](../../UBONGO_BUILD.md) per phase). User merges to `main` after testing plan + smoke test pass. Don't commit to `main` from a phase in progress; don't self-merge.
- **Memory Agent as single writer.** No agent writes durable state directly. Patterns that bypass this should be rejected at review time.
- **Every workflow goes through the queue.** Even synchronous CLI responses. The seam supports v0.2 Telegram and v0.3 proactive jobs without restructuring.
- **Every governance decision and every evolution variant is persisted.** Tracing is not optional.
- **Progressive disclosure for skills.** Skill *descriptions* load at startup; *bodies* load on activation. Verifiable in logs.
- **Hierarchical context.** System prompts assembled per turn from `config/UBONGO.md` (global) + active persona + active skill body + worker role frame. Closer-to-task layers come last.
- **Plans archived in `Plans/`.** After plan-mode plans are approved, copy them into the project's `Plans/` folder so they're readable from the project itself.

## Tasks Finished

- Removed duplicate `Ubongo_PRD.md` (it was a byte-for-byte copy of `Ubongo.md`).
- Renamed `Ubongo.md` → `UBONGO_VISION.md` and added a top-of-file disclaimer.
- First scope pivot: rewrote docs for CLI-first v0.1 (Telegram deferred to v0.2). Edited UBONGO_BUILD.md throughout.
- Second scope pivot: full rewrite of `UBONGO_BUILD.md` for multi-agent + GP self-improving v0.1. New architecture, new file structure, new schema, 22-phase build plan with sub-phases + testing plans + end-to-end smoke tests + branch names per phase.
- Created [CLAUDE.md](../../CLAUDE.md) at repo root.
- Created [STATUS.md](../../STATUS.md) with 22-row phase tracker.
- Created `.gitignore` (covers `.env`, `*.db`, `vault/`, Python, IDE state, etc.).
- Created `Plans/` directory with both archived plans.
- Created `tests/manual/smoke_test.md` stub playbook with sections per phase.
- Final README revision: tightened opening, added "How It Works" ASCII flow, grouped slash commands by purpose under sub-headings, added Implementation Workflow section about branch-per-phase, clarified pre-implementation status with blockquotes.
- Saved 4 memory files capturing user preferences and updated `MEMORY.md` index.

## Files Modified

| File | Changes | Rationale |
| --- | --- | --- |
| `UBONGO_BUILD.md` | Full rewrite for multi-agent + GP v0.1 spec. ~700 lines. Adds 22 detailed phases with sub-phases, testing tables, and per-phase smoke tests. | User redirected v0.1 scope to include full multi-agent + self-improving runtime. |
| `README.md` | Restructured: tighter opener, new "How It Works" section, slash commands grouped by sub-heading, new "Implementation Workflow" section, branched-status callouts. | Make the new scope readable; surface the branch-per-phase rule. |
| `CLAUDE.md` | Created. Contains project description, what's in/out, conventions, architectural rules, branch workflow, build phases overview, LOC budget. | Required by `UBONGO_BUILD.md` but didn't exist; provides Claude Code session context. |
| `STATUS.md` | Created. 22-row phase tracker with branch names, ~15k LOC budget, full acceptance-criteria checklist. | Live tracker for phase progress. |
| `UBONGO_VISION.md` | Renamed from `Ubongo.md`. Disclaimer at top softened from "not v0.1 spec" to "design exposition v0.1 realizes." | Reflects the new maximalist v0.1 scope. |
| `.gitignore` | Created. | Cover secrets / SQLite / vault / Python / IDE state. |
| `Plans/please-revise-the-documentation-staged-rossum.md` | Created (copy from `~/.claude/plans/`). | Archive the CLI doc-revision plan in the project. |
| `Plans/v0.1-redesign-multi-agent-self-improving.md` | Created (copy from `~/.claude/plans/`). | Archive the multi-agent redesign plan in the project. |
| `tests/manual/smoke_test.md` | Created. Cumulative end-to-end manual playbook with one section per phase. Phase 0 and Phase 1 stubs filled. | Per user requirement that each phase ends with the entire system manually testable. |

Memory files (under `~/.claude/projects/-Volumes-giuseppeM1mini-External-Coding-ubongo/memory/`):

| File | Content |
| --- | --- |
| `MEMORY.md` | Index pointing at the four memory files below. |
| `feedback_ubongo_v0.1_full_vision.md` | v0.1 = multi-agent + GP self-improving runtime; hand-rolled; ~15k LOC; 22 phases. Supersedes any "lean v0.1" framing. |
| `feedback_ubongo_cli_first.md` | CLI-only channel for v0.1; Telegram → v0.2. Channel constraint only; doesn't constrain v0.1 brain scope. |
| `feedback_branch_per_phase.md` | Each implementation phase on its own branch; user approves and merges. Don't self-merge. |
| `feedback_plans_folder.md` | Plans archived in project `Plans/` after plan-mode approval. |

`Ubongo_PRD.md` was removed via `git rm` (was a byte-for-byte duplicate of the original `Ubongo.md`).

## Decisions Made

| Decision | Options Considered | Rationale |
| --- | --- | --- |
| CLI is the v0.1 channel | Telegram-first vs CLI-first | User chose CLI to validate behavior synchronously without external-service complexity; Telegram deferred to v0.2. |
| Notification policy engine + quiet hours + holds → v0.2 | Keep partially in v0.1 vs full minimal queue only vs defer all | Designed around Telegram proactive delivery; doesn't apply to a synchronous CLI. Minimal queue stays so v0.3 scheduler has somewhere to write. |
| Multi-agent depth: full vision | Light (Master + 4 workers) / Medium (7 workers) / Full | User chose Full Vision: Master + 8 workers, all 6 execution modes including speculative. |
| Self-improvement: continuous GP loop with human approval | Manual variant testing / background with approval gates / continuous GP | User chose continuous GP. Generations autonomous, sandboxed eval, human-approved promotions. |
| Orchestration: hand-rolled | Hand-rolled / LangGraph / LangGraph-light | User chose hand-rolled. Plain Python `asyncio` + event bus. Aligns with toy-project ethos and keeps deps minimal. |
| LOC budget: ~15,000 (soft) | 2500 (original) / ~15k / no cap | The maximalist scope makes 2500 obsolete. ~15k is realistic. Soft ceiling, not a constraint to over-cut against. |
| Branch per implementation phase | Direct to main / feature branches / phase branches | User explicit instruction: each phase on its own branch (`phase-N-<short-name>`); user merges to main after approval. |
| 22 phases in 6 tiers | Fewer big phases / many granular phases | User wanted sub-phases + testing plans + end-to-end testability per phase. 22 small phases satisfy "each phase ends with the entire system manually testable end-to-end" cleanly. |
| Single `UBONGO_BUILD.md` (not split into `docs/`) | Monolithic vs split (AGENTS.md, SELF_IMPROVEMENT.md, GOVERNANCE.md) | Default chosen: keep monolithic for one source of truth. User did not push back. |
| Smoke test as Markdown playbook | Markdown vs pytest-driven | User said "manually test." Markdown playbook is right; pytest scripts can be added later. |
| Plans archived in project `Plans/` | Only in `~/.claude/plans/` vs also in project | User explicit instruction: copy approved plans into project `Plans/` for readability. |

## Immediate Next Steps

1. **Start Phase 0 on branch `phase-0-skeleton`.** Run `git checkout -b phase-0-skeleton`. Read [UBONGO_BUILD.md → Phase 0](../../UBONGO_BUILD.md) for sub-phases (0a–0e), files to touch, and the testing plan. Sub-phases: project init (`uv init`, `pyproject.toml` with `litellm`/`python-dotenv`/`pyyaml`/`pytest`/`sqlite-vec`); config loading; hierarchical context loader; structured JSON logging; CLI entry point.
2. **At Phase 0 end**, run the testing plan from [UBONGO_BUILD.md](../../UBONGO_BUILD.md) (4 scenarios). Surface results to the user. Do not merge — user merges.
3. **After Phase 0 merges**, branch `phase-1-cli-echo` for the REPL + one-shot echo phase. Append Phase 1's smoke test scenarios to [tests/manual/smoke_test.md](../../tests/manual/smoke_test.md) (the playbook starts being meaningful at Phase 1).

## Blockers / Open Questions

- [ ] **No commit yet for the doc work.** All this session's documentation work is uncommitted on `main`. User has not asked for a commit. Decide with user whether to commit before branching `phase-0-skeleton`, or branch off the dirty tree (will require `git stash` or committing the docs first).
- [ ] **OpenRouter model names need verification** before Phase 2 (LLM integration). [UBONGO_BUILD.md](../../UBONGO_BUILD.md) settings.yaml example references `openrouter/anthropic/claude-sonnet-4.5`, `openrouter/anthropic/claude-haiku-4.5`, `openrouter/qwen/qwen-2.5-7b-instruct`, `openrouter/openai/text-embedding-3-small`. Verify against the live OpenRouter catalog at Phase 2 start; substitute current names if needed. Don't fail the implementation on a stale model name.
- [ ] **Held-out conversation sample** for evolution evaluation (`tests/manual/fixtures/sample_conversations.json`) does not exist yet. Phase 17 requires curating ~30 short anonymized conversations. Plan ahead: keep an eye on real conversations during Phases 2–6 that could be candidates (with user permission); curate before Phase 17.
- [ ] **Sandbox enforcement details** for Execution Agent (Phase 15) need fleshing out at implementation time: exact filesystem allowlist, exact env subset, network-block mechanism. The spec gives the contract; the implementation requires real testing on the user's macOS environment.

## Deferred Items

- **Telegram channel** → v0.2. Brings back `python-telegram-bot`, `allowed_user_ids` auth, the policy engine + quiet hours + holds + catch-up summarizer. Should be additive: a transport plus a `before_send` policy handler.
- **External integrations** → v0.2+ (Calendar, Gmail, Reddit, news), each as a CLI script invoked through the constrained-bash skill.
- **Production observability dashboards** → not on roadmap. Structured logs + `/audit` are enough for v0.1.
- **Spec gaps** flagged in earlier exploration (skill-trigger precedence between command/classifier/manual; session-timeout edge cases at 29:59 vs 30:01; OpenRouter rate-limit error handling; security model for API keys). Real but minor; address case-by-case during implementation.

## Important Context

Read these before doing anything:

1. The four **memory files** in `~/.claude/projects/-Volumes-giuseppeM1mini-External-Coding-ubongo/memory/` — they encode the user's preferences and the v0.1 scope decisions. They auto-load. Trust them but verify against current docs if the docs say something different.
2. [UBONGO_BUILD.md](../../UBONGO_BUILD.md) — the source of truth. Phase 0 starts at `### Phase 0 — Skeleton`.
3. [CLAUDE.md](../../CLAUDE.md) — conventions and architectural rules. The "Architectural Rules" section is non-negotiable (Master Agent orchestrates; Memory Agent single writer; everything through the queue; every decision/run/variant persisted; etc.).
4. [STATUS.md](../../STATUS.md) — confirm current phase status. As of this handoff, all 22 rows are "Not started."

User communication preferences (also encoded in `config/UBONGO.md` once it exists, also in [CLAUDE.md](../../CLAUDE.md)):

- Direct prose, no hedging.
- Default to prose over bullets unless a list is genuinely list-shaped.
- No em-dashes.
- No emojis unless the user uses them first.
- Minimal markdown in conversational output.

Branch workflow (mandatory):

- Each implementation phase on its own branch: `phase-N-<short-name>`. Names are listed per phase in [UBONGO_BUILD.md](../../UBONGO_BUILD.md) and [STATUS.md](../../STATUS.md).
- Don't commit to `main` from a phase in progress.
- Don't merge yourself; the user merges. Signal completion with the testing-plan + smoke-test results.
- Don't start phase N+1 until phase N is merged.

LOC budget: soft ~15,000 (excluding tests). The prior 2500-line target is obsolete; do not optimize against it.

## Assumptions Made

- The repo is git-init'd and on `main` (verified during this session). Doc changes since the initial commit are uncommitted.
- `uv` is available on the user's system (the user installed it; the scripted check at Phase 0 will confirm).
- The user has an OpenRouter API key. The setup instructions tell them to set `OPENROUTER_API_KEY` in `.env`.
- The user's OS is macOS (Darwin per environment); paths and shell commands assume macOS conventions.
- Project working directory is `/Volumes/giuseppeM1mini-External/Coding/ubongo` (an external drive). Read/write speeds may be slower than internal disk; tests should account for that if they're sensitive to disk latency.

## Potential Gotchas

- **External edits during work.** During this session, the user / a markdown linter modified files (e.g., `UBONGO_BUILD.md` and `README.md`) while I was working. If a `Write` fails with "file modified," `Read` again before retrying. Trust the on-disk state.
- **`.specstory/` and `.history/` are gitignored** but were already tracked in git history when the repo was initialized. Future commits won't add new ones, but the existing tracked files still show in `git ls-files`. Don't `git rm` them unless the user explicitly asks.
- **Memory file lint warnings (MD041) are by design.** The session-handoff and memory files start with YAML frontmatter, not a `# heading`, per the memory subsystem's spec. Ignore those warnings.
- **The repo had `.git` already** by the time this session started — environment context said "not a git repo" but it actually is. Confirm with `git status` before assuming.
- **`Ubongo.md` and `Ubongo_PRD.md` no longer exist** at the project root. They've been replaced by `UBONGO_VISION.md` (former `Ubongo.md` content with disclaimer) and a delete (`Ubongo_PRD.md`, duplicate). Don't reference them in new work.
- **Don't re-introduce the "What Ubongo Is Not" exclusion of multi-agent / self-improving.** Those are now what Ubongo IS, per the v0.1 redesign. The "is not" list now covers only multi-channel / production / multi-user / distributed.
- **`config/UBONGO.md` doesn't exist yet.** It's referenced throughout the spec but is created in Phase 0 (sub-phase 0e or thereabouts). Phase 0's testing plan exercises it.
- **Plans/ is in the project root and is NOT gitignored** (intentional — user wants plans committed). Don't add it to `.gitignore`.
- **`vault/` IS gitignored.** When implementing Phase 5+ (vault projection), do not commit generated daily notes.
- **The Master Agent's `decide` returns `auto` until Phase 14**. Phases 8–13 ship the seam without the rules. Tests written in earlier phases should not assert the eventual rule-driven behavior.

## Environment State

### Tools / Services Used

- **uv** (Astral, https://docs.astral.sh/uv/) — package manager. Will be used at Phase 0 to scaffold `pyproject.toml`.
- **OpenRouter** (https://openrouter.ai/) — model provider. Required at Phase 2.
- **Obsidian** — for browsing the Markdown vault generated from Phase 5 onward. Optional — vault is plain Markdown.
- **git** — used in this session for `git rm` and `git mv`. Repo is initialized but session changes are uncommitted.

### Active Processes

- None. No long-running processes started in this session.

### Environment Variables

- `OPENROUTER_API_KEY` — needed from Phase 2 onward; loaded from `.env` via `python-dotenv`. Do not log the value.
- `TELEGRAM_BOT_TOKEN`, `GOOGLE_CALENDAR_*`, `GMAIL_*`, `REDDIT_*` — listed in `.env.example` for v0.2+ phases. Not needed for v0.1.

## Related Resources

- `UBONGO_BUILD.md` — primary build spec (project root).
- `CLAUDE.md` — Claude Code session context (project root).
- `STATUS.md` — phase tracker (project root).
- `README.md` — human-facing entry point (project root).
- `UBONGO_VISION.md` — design exposition (project root).
- `Plans/please-revise-the-documentation-staged-rossum.md` — plan from the CLI doc-revision pivot.
- `Plans/v0.1-redesign-multi-agent-self-improving.md` — plan from the multi-agent redesign pivot. Read this for the architecture decision rationale.
- `tests/manual/smoke_test.md` — cumulative end-to-end manual playbook.
- Memory files at `~/.claude/projects/-Volumes-giuseppeM1mini-External-Coding-ubongo/memory/` — user preferences and project decisions. Auto-loaded.

---

**Security Reminder**: Before finalizing, run `validate_handoff.py` to check for accidental secret exposure.
