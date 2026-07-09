# Ubongo — Context for Claude Code Sessions

Ubongo is a personal, intent-routed AI mind for one user (Giuseppe Turitto), running locally and CLI-first. It is **a multi-agent orchestration platform** and **a self-improving runtime** in one package: a Master Agent runs every turn through a fixed governed pipeline, dispatching a fleet of disposable worker agents across six execution modes; a governance layer gates risk and reversibility; a continuous Genetic Programming loop evolves prompts and routing/tool-chain/retry config, and a second loop drafts new skills — both behind a human approval boundary. What began as a CLI is now reachable over **six additive channels** (REPL, one-shot, Streamlit web, MCP server, Telegram, and a streaming browser console), each a thin adapter over one turn seam. Nothing the system produces about itself, and no new external reach, goes live without explicit human approval.

## Status and Source of Truth

The living state docs are regenerated and are the freshest truth — read these first:

- [PROJECT_STATUS.md](PROJECT_STATUS.md) — fast catch-up: plan position, what works, what's missing.
- [PROJECT_STATE.md](PROJECT_STATE.md) — full state for "what to build next" strategy.
- [PROJECT_ARCHITECTURE.md](PROJECT_ARCHITECTURE.md) — component map, seams, decisions, the core invariant.
- [CONTEXT.md](CONTEXT.md) — domain glossary. [docs/adr/](docs/adr/) — 23 accepted ADRs, the settled decisions.
- [STATUS.md](STATUS.md) / [STATE.md](STATE.md) — the v0.1-era phase changelog. **Historical** (last current at v0.1.5); use the `PROJECT_*` docs for anything after v0.1.

Where we are (verify the exact number in PROJECT_STATUS.md — versions move fast): **v0.1** (the 22-phase CLI build) is certified on `main`, plus a post-v0.1 layer (web UI, self-authored skills, profiler, MCP server, MCP client / Connector). **v0.5 — the trust protocol** is complete and merged on `main` (outer egress envelope, store split, resumable approval seam, Telegram, grant registry, standing jobs, contract/identity). The active line is **v0.6 — the live console** ([Plans/v0.6-live-console.md](Plans/v0.6-live-console.md)), a six-phase streaming-UI plan; Phase 00 (the streaming seam) is built and in review.

The original v0.1 build spec is [UBONGO_BUILD.md](UBONGO_BUILD.md) (source of truth for v0.1 scope only); the conceptual origin is [UBONGO_VISION.md](UBONGO_VISION.md). Work after v0.1 is plan-driven under [Plans/](Plans/), one numbered phase line per plan.

## What Ubongo Is

- A multi-agent orchestration platform (Master Agent + workers: Research, Coding, Evaluator, Critic, Execution, Repair, Memory, three Personas, and the Connector).
- A self-improving runtime (a continuous GP loop over prompts/config and an authoring loop over new skills, both sandboxed and human-approved).
- A six-channel, CLI-first system for one user, locally (REPL + one-shot, plus web, MCP, Telegram, and the streaming console as additive adapters).
- A memory-centric system (SQLite canonical, Markdown vault projected, embeddings indexed via `sqlite-vec`, vault-link graph), with one writer.

## What Ubongo Is Not

- A production system or SaaS product.
- A multi-user or team tool. One user, single-flight.
- A distributed system (no Docker-in-app, no Kubernetes, no Temporal, no Redis) and no orchestration framework (no LangGraph, no Ray) — hand-rolled asyncio plus an event bus.
- Indiscriminately multi-channel. Channels are added deliberately, one additive adapter at a time. Telegram shipped in v0.5 (ADR-0020); the user prefers privacy-respecting platforms (Signal/Matrix over Meta/Facebook) — confirm the exact platform before building any new messaging channel.

A feature with no home in a current `Plans/` phase or a prior accepted ADR is out of scope. The trust posture is single-user LAN / private-relay: no per-request auth or TLS in-app.

## Conventions

User communication preferences (also in `config/UBONGO.md`):

- Direct prose, no hedging.
- Default to prose over bullets unless a list is genuinely list-shaped.
- No em-dashes.
- No emojis unless the user uses them first.
- Minimal markdown in conversational output.

## Development Workflow: ForgeLoop

The repo develops under the ForgeLoop standard (ADR-0023). The tool-agnostic operating spine is [AGENTS.md](AGENTS.md) — source-of-truth order, work classification, rigor modes, tool modes, the non-negotiables — and the documentation map is [docs/00-index.md](docs/00-index.md). Every new plan states its work classification and rigor mode in its header; trust-spine work is `Strict` minimum. "Rigor mode" is ForgeLoop's ceremony tier, not the WorkflowRunner's six execution modes (the rename is recorded in [CONTEXT.md](CONTEXT.md)).

## Architectural Rules

- **One turn seam, no bypass.** Every turn, from every channel, enters through `channel.run_turn` (`bootstrap()` loads config/logging once; the channel layer is presentation only). No channel handles a turn its own way.
- **Master Agent orchestrates**: classify → plan → execute (workflow runner) → govern → compose → commit → enqueue. No bypass paths.
- **Memory Agent is the only writer** to durable memory (SQLite, vault, embeddings, and the subsystem state tables). Other agents return findings; Memory Agent commits.
- **Every outbound message goes through `notification_queue`**, even synchronous CLI replies. Telegram, the streaming console, and proactive standing-jobs output all inherit this seam.
- **Every workflow / agent run / governance decision / evolution variant is persisted**. Tracing is not optional.
- **Secrets only in `.env`**. Config never contains secrets.
- **New channels are additive over `channel.run_turn`.** New behavior ships as event handlers registered on the named events in `src/ubongo/events.py` (canonical list there) — `before_classify` / `after_classify`, `before_plan` / `after_plan`, `before_execute` / `after_execute`, `before_govern` / `after_govern`, `before_compose` / `after_compose`, `before_send` / `after_send`, `before_llm` / `after_llm`, `agent_started` / `agent_completed` / `agent_failed`, `evolution_generation` / `evolution_promotion`. The console's streaming and standing jobs' proactive policy are both event handlers, not pipeline edits.
- **New tools default to CLI scripts invoked through the constrained-bash skill**, not first-class tool definitions. First-class tools require justification (deferred by ADR-0016).
- **External reach is one door each way.** Inbound is the MCP server (`mcp/server.py`); outbound is the **Connector agent only** (`agents/connector.py` over `mcp/client.py`, ADR-0016) — opt-in via `/mode connector_session`, scored irreversible, risk per server.
- **Shell-execution safety lives in `src/ubongo/sandbox.py`, not in `SKILL.md` bodies** (ADR-0005). A SKILL.md body is markdown the LLM-side reads; anything that affects what runs on the user's machine is enforced in code the LLM cannot rewrite — explicit allowlist, no shell metacharacters, no path traversal, empty child PATH, repo-root cwd, 10s timeout. The seam stays in one module; the allowlist is a human-only change.
- **Composer attribute on agents.** `WorkflowResult.text` comes from the last agent whose class declares `composer = True` (read via `getattr(agent, "composer", False)`). Validators (Evaluator, Critic) and helpers (Research, Execution, Connector) contribute `prior_findings` without claiming the response.
- **The trust spine.** Egress control sits below the app (the outer envelope, ADR-0017: rootless Podman + nftables on Linux/Pi). A gated turn is a persisted, resumable `pending_approvals` record approvable from any channel (ADR-0018). Standing consent is the grant registry, checked *after* the safety rules (ADR-0019).
- **Hand-rolled orchestration.** No LangGraph, no Temporal, no Ray. Plain Python with `asyncio` and an event bus (ADR-0001).
- **Self-modification is approved, not autonomous.** The GP loop and the authoring loop run in the background but boot **paused**; no variant promotes and no authored skill becomes discoverable without explicit user approval (`/improvements`, `/skill-candidates`). The four daemons share one `DaemonLoop` lifecycle.

## The Core Invariant

One governed seam per kind of consequence, with a human gate on self-modification and on first external reach. Turns through `master.handle`; durable writes through the Memory Agent; outbound through the queue; external calls through the Connector; shell through `sandbox.py`; approvals through the one `pending_approvals` record. A change that routes around any of these seams, or lets a loop promote its own output, violates the architecture's reason for existing.

## Branch Workflow

Every implementation phase is built on its own branch, off the latest `main`:

- Branch name: `vX.Y/NN-<short-name>` (e.g. `v0.6/00-streaming-seam`, `v0.5/04-telegram`). The version rides the branch name (`v0.X/NN-name` → `0.X.NN`) and CI bumps it on merge (`release.sh` / `.github/workflows/release.yml` tags from `VERSION` on `main`). Earlier v0.1 phases used `phase-N-<name>`; that scheme is historical.
- **Open the GitHub PR as a draft right after the first commit on the branch** (typically the `Plan: ...` commit), base `main`, title for the phase, body linking the plan in `Plans/`. Keep it draft until the phase's testing plan + smoke test pass; then mark ready. The PR is the live review surface that grows commit-by-commit.
- All commits for that phase land on the branch. Do not commit to `main` from a phase in progress.
- "Prepare a new phase" is a docs-only PR: branch, plan in `Plans/` (with its smoke test), and a draft PR — no implementation until the plan is approved.
- The user reviews when the phase's testing plan and smoke test pass, and the user merges the branch into `main`. Do not merge yourself. ("Merge the PR" means merge the branch into `main`.)
- Don't start phase N+1 until phase N's branch is merged. Bump the version before merge.

## Plans and Phases

v0.1 was 22 phases across six tiers (Foundation, Multi-Agent, Self-Healing, Governance, Self-Improvement, Wiki Memory + Polish) — full detail in [UBONGO_BUILD.md](UBONGO_BUILD.md), historical changelog in [STATUS.md](STATUS.md). Everything since is plan-driven: each plan in [Plans/](Plans/) is a numbered phase line (v0.5 trust protocol, v0.6 live console). For the active line and current position, read [PROJECT_STATUS.md](PROJECT_STATUS.md) and the relevant `Plans/v0.X-*.md`. Phases are strictly ordered; don't start N+1 until N is merged.

## LOC Budget

Soft target: under ~15,000 lines of Python (excluding tests). The codebase is now **~17,400 LOC, roughly 16% over** — the full multi-agent + GP + trust + multi-channel scope has outgrown the target, and the rule is "cut, don't expand," not raise the ceiling. The one scheduled clawback is v0.6 Phase 05 (retire Streamlit). Treat new subsystems as budget pressure: a new *transport* over an existing seam is cheap; a new *subsystem* widens the gap and needs justification.

## Testing

Each phase has a testing plan with concrete scenarios in its `Plans/` doc (v0.1's are in [UBONGO_BUILD.md](UBONGO_BUILD.md)). End-to-end manual testability after every phase: the cumulative playbook lives at `tests/manual/smoke_test.md` and grows phase by phase. Pytest covers roughly one module per source module (~1,057 tests). Held-out conversation samples for evolution evaluation live at `tests/manual/fixtures/sample_conversations.json`. Note: enforcement increasingly lives *outside* the suite — the egress envelope and the live relays (Telegram, console SSE) are real boundaries pytest cannot see; a green suite certifies less of the trust/operational posture than it did at v0.1.

## Development Environment

- OS: macOS (Darwin 25.5.0)
- Shell: /bin/zsh
- Path format: Unix
- File system: Case-sensitive (default)
- Line endings: LF
- Python 3.11+ under uv.

## Agent skills

### Issue tracker

Issues and PRDs live as GitHub issues (`gh` CLI), repo `GTuritto/ubongo`. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical triage roles, default label strings (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
