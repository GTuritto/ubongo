# Ubongo — Context for Claude Code Sessions

Ubongo is a personal, mood-aware AI mind for one user (Giuseppe Turitto), running locally as a CLI. v0.1 is **a multi-agent orchestration platform** and **a self-improving runtime** in one package: a Master Agent dispatches a fleet of disposable worker agents across six execution modes; a governance layer gates risk; a continuous Genetic Programming loop evolves prompts, routing rules, tool chains, and retry strategies, with human approval before any promotion. CLI is the v0.1 channel (REPL plus one-shot); Telegram is v0.2.

## Status

See [STATUS.md](STATUS.md) for current phase progress. As of 2026-05-14: Phases 0 through 11 complete and merged to `main` (~4,925 LOC under `src/`, 326 / 326 pytest green, full Phases 0-11 smoke playbook passes). Ten worker agents are registered. Sequential execution mode only; Phase 12 brings the other five. The build runs across 22 phases with sub-phases and per-phase testing plans in [UBONGO_BUILD.md](UBONGO_BUILD.md).

The build specification is [UBONGO_BUILD.md](UBONGO_BUILD.md). Treat it as the source of truth for v0.1 scope. The conceptual origin is [UBONGO_VISION.md](UBONGO_VISION.md) — the design exposition that v0.1 now realizes.

## What Ubongo Is

- A multi-agent orchestration platform (Master Agent + worker agents: Research, Coding, Evaluator, Repair, Memory, Critic, Execution, Persona).
- A self-improving runtime (continuous GP loop with sandboxed evaluation and human-approved promotions).
- A CLI (REPL + one-shot) for one user, locally.
- A memory-centric system (SQLite canonical, Markdown vault projected, embeddings indexed via `sqlite-vec`, vault-link graph).

## What Ubongo Is Not (v0.1)

- A multi-channel system. CLI only in v0.1; Telegram is v0.2; Slack/WhatsApp/Discord/web/voice are not on the roadmap.
- A production system or SaaS product.
- A multi-user or team tool.
- A distributed system (no Docker, no Kubernetes, no Temporal, no Redis).

If a feature isn't explicitly listed in `UBONGO_BUILD.md`'s 22 phases or its acceptance criteria, it's out of scope for v0.1.

## Conventions

User communication preferences (also in `config/UBONGO.md`):

- Direct prose, no hedging.
- Default to prose over bullets unless a list is genuinely list-shaped.
- No em-dashes.
- No emojis unless the user uses them first.
- Minimal markdown in conversational output.

## Architectural Rules

- **Master Agent orchestrates**: classify → plan → execute (workflow runner) → governance gate → compose → enqueue. No bypass paths.
- **Memory Agent is the only writer** to durable memory (SQLite, vault, embeddings). Other agents return findings; Memory Agent commits.
- **Every outbound message goes through `notification_queue`**, even synchronous CLI responses. Telegram (v0.2) and proactive jobs (v0.3) inherit this.
- **Every workflow / agent run / governance decision / evolution variant is persisted**. Tracing is not optional.
- **Secrets only in `.env`**. Config never contains secrets.
- **New behavior in v0.2+ ships as event handlers** registered on the named events (`before_classify`, `after_classify`, `before_plan`, `after_plan`, `before_execute`, `after_execute`, `before_govern`, `after_govern`, `before_compose`, `after_compose`, `before_send`, `after_send`, `agent_started`, `agent_completed`, `agent_failed`, `evolution_generation`, `evolution_promotion`).
- **New tools default to CLI scripts invoked through the constrained-bash skill**, not first-class tool definitions. First-class tools require justification.
- **Shell-execution safety lives in `src/ubongo/sandbox.py`, not in `SKILL.md` bodies.** A SKILL.md body is markdown the LLM-side reads; anything that affects what runs on the user's machine must be enforced in code that the LLM cannot rewrite. Phase 11 ships an explicit allowlist + no shell metacharacters + no path traversal + restricted PATH + repo-root cwd + 10s timeout. Phase 15 will harden further; the seam stays in one module.
- **Composer attribute on agents (Phase 10).** `WorkflowResult.text` comes from the last agent whose class declares `composer = True` (read via `getattr(agent, "composer", False)`). Validators (Evaluator, Critic) and helpers (Research, Execution) contribute `prior_findings` without claiming the response.
- **No Telegram-specific code in v0.1.** When Telegram lands in v0.2, it should be additive: a new transport, a `before_send` policy handler, restored `allowed_user_ids` auth.
- **Hand-rolled orchestration.** No LangGraph, no Temporal, no Ray. Plain Python with `asyncio` and an event bus.
- **GP-driven self-improvement is approved, not autonomous.** The loop runs in the background, but no variant promotes to production without explicit user approval via `/improvements`.

## Branch Workflow

Every implementation phase (Phase 0 through Phase 21) is built on its own branch:

- Branch name: `phase-N-<short-name>` (e.g., `phase-0-skeleton`, `phase-8-master`, `phase-18-gp-loop`). Names are listed in [UBONGO_BUILD.md](UBONGO_BUILD.md) per phase.
- Branch off the latest `main` at phase start.
- **Open the GitHub PR as a draft right after the first commit on the branch** (typically the `Plan: ...` commit), base `main`, title `Phase N — <Phase title>`, body links the plan in `Plans/`. Keep it draft until the phase's testing plan + smoke test pass; then mark ready for review. The PR is the live review surface that grows commit-by-commit, not a forum that materializes only at the end.
- All commits for that phase land on the branch. Do not commit to `main` from a phase in progress.
- The user reviews when the phase's testing plan and smoke test pass.
- The user merges the branch into `main`. Do not merge yourself.
- Don't start phase N+1 until phase N's branch is merged.

## Build Phases (overview)

22 phases organized into 6 tiers; full detail in [UBONGO_BUILD.md](UBONGO_BUILD.md).

- **Tier 1 — Foundation (0–7):** skeleton, CLI echo, LLM, classifier, memory, vault, skills, queue.
- **Tier 2 — Multi-Agent System (8–12):** Master Agent, workers (Research/Memory, then Evaluator/Critic/Personas, then Coding/Execution/Repair), all six execution modes.
- **Tier 3 — Self-Healing (13):** Repair Agent activated.
- **Tier 4 — Governance (14–15):** risk + confidence scoring, approval gates + sandboxing.
- **Tier 5 — Self-Improvement (16–19):** variant generation, sandboxed evaluation + fitness, GP loop, target expansion + promotions.
- **Tier 6 — Wiki Memory + Polish (20–21):** embeddings + graph, bidirectional vault sync + audit.

Don't start Phase N+1 until Phase N's testing plan and smoke test pass and the branch is merged.

## LOC Budget

Soft target: under ~15,000 lines of Python (excluding tests). The full multi-agent + GP scope makes the prior 2500-line target obsolete; 15k is realistic. If significantly over, the spec is doing too much and the answer is to cut, not to expand the budget.

## Testing

Each phase has a testing plan with concrete scenarios in [UBONGO_BUILD.md](UBONGO_BUILD.md). End-to-end manual testability after every phase: the cumulative playbook lives at `tests/manual/smoke_test.md` and grows phase by phase. Pytest tests for each module are listed in the spec's `tests/` layout. Held-out conversation samples for evolution evaluation live at `tests/manual/fixtures/sample_conversations.json` (curated, anonymized).

## Development Environment

- OS: macOS 25.4.0
- Shell: /bin/zsh
- Path format: Unix
- File system: Case-sensitive (default)
- Line endings: LF

## Agent skills

### Issue tracker

Issues and PRDs live as GitHub issues (`gh` CLI), repo `GTuritto/ubongo`. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical triage roles, default label strings (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
