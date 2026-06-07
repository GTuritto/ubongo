# Ubongo

A personal, mood-aware AI mind that runs as a local CLI. *Ubongo* is Swahili for *brain* or *mind*.

v0.1 packs three things into one system, hand-rolled in plain Python:

- **A multi-agent orchestrator.** A Master Agent classifies each turn, plans a workflow, dispatches a fleet of worker agents (Research, Coding, Evaluator, Repair, Memory, Critic, Execution, Persona), and composes the response. Six execution modes are available: sequential, parallel, competitive, collaborative, debate, speculative.
- **A self-improving runtime.** A continuous Genetic Programming loop evolves prompts, routing rules, tool chains, and retry strategies. Variants are evaluated against held-out conversation samples; lineage is tracked; promotions to production require explicit user approval via `/improvements`.
- **A memory-centric local system.** SQLite is the canonical store. An Obsidian-compatible Markdown vault is projected from it. `sqlite-vec` indexes messages and vault notes for semantic recall. Vault links form a graph the Master Agent can traverse.

A governance layer evaluates risk, confidence, and reversibility per turn and gates risky actions through user approval. Single user, single machine, single channel. No LangGraph, no Temporal, no Docker.

## Status

**v0.1 is complete — all 22 phases (0–21) merged to `main` and certified.** The CLI runs end-to-end: classify, plan, execute through the worker fleet, govern, compose, enqueue, persist. Ten worker agents are registered (Architect, Operator, Casual personas + Research, Memory, Evaluator, Critic, Coding, Execution, Repair); all six execution modes are live; the Repair Agent walks a full recovery ladder; the governance decision matrix gates risky turns through an interactive `y/n/why` approval flow over a hardened sandbox. The Genetic Programming loop is closed: it generates variants (of persona prompts *and* routing rules / tool chains / retry config), evaluates them against held-out samples, evolves generations autonomously, and proposes promotions that take effect via a live swap only after you approve them in `/improvements`. Semantic recall (`sqlite-vec`) augments recency, a vault-link graph is queryable, and a polling watcher ingests edits you make to vault notes (bidirectional sync) with a unified audit log. **723 / 723 tests green; ~11,255 LOC** (under the 15k soft target); the full cumulative smoke passes end-to-end.

The build ran across **22 phases in 6 tiers**. Each phase was implemented on its own branch, shipped a working system, and ended with a manual end-to-end smoke test before merging to `main`. See [STATUS.md](STATUS.md) for the phase-by-phase changelog and [UBONGO_BUILD.md](UBONGO_BUILD.md) for sub-phases and per-phase testing plans. Next is **v0.2 (Telegram)** — a new transport, additive on the existing event/queue seams.

## What Ubongo Is

- A multi-agent orchestration platform for one user, locally.
- A self-improving runtime: GP loop with sandboxed evaluation, lineage tracking, human-approved promotions.
- A CLI: REPL primary, one-shot for scripting.
- A memory-centric system: SQLite canonical, Markdown vault projected, embeddings indexed, graph linked.

## What Ubongo Is Not (v0.1)

- A multi-channel system. CLI only in v0.1; Telegram is v0.2; Slack/WhatsApp/Discord/web/voice are not on the roadmap.
- A production system or SaaS product.
- A multi-user or team tool.
- A distributed system. Single process, single machine.

## How It Works (one screen)

```text
CLI input
   │
   ▼  classify ─────► intent, tone, risk, suggested skill, confidence
   │
   ▼  plan ─────────► Workflow(agents, execution mode, persona, models)
   │
   ▼  execute ──────► Workflow Runner spawns agents (sequential / parallel /
   │                  competitive / collaborative / debate / speculative);
   │                  Evaluator aggregates
   │
   ▼  govern ───────► auto / ask_clarification / require_approval / reject
   │                  (decision matrix: intent + risk + confidence +
   │                  reversibility + preferences + context)
   │
   ▼  compose ──────► Persona Agent shapes the final user-facing text
   │
   ▼  enqueue ──────► notification_queue (urgent for synchronous responses)
   │
   ▼  send ─────────► dequeue and print to stdout
   │
   ▼  remember ─────► Memory Agent writes SQLite + vault + embeddings
```

A continuous Genetic Programming loop runs in the background on its own asyncio task: generate variants of a target (prompt, routing rule, tool chain, retry strategy), evaluate them in a sandbox against held-out samples, score with a configurable fitness function, surface winners to the user via `/improvements` for approval. Nothing promotes to production without a human `approve`.

## Documentation Map

- [README.md](README.md) — this file. Goal, setup, usage, roadmap, contributing.
- [docs/system-architecture.md](docs/system-architecture.md) — current implementation architecture with Mermaid diagrams (runtime flow, events, data model).
- [UBONGO_BUILD.md](UBONGO_BUILD.md) — full v0.1 build specification, 22 phases with sub-phases and per-phase testing plans. Source of truth.
- [UBONGO_VISION.md](UBONGO_VISION.md) — design exposition the v0.1 build realizes.
- [CLAUDE.md](CLAUDE.md) — context for Claude Code sessions.
- [STATUS.md](STATUS.md) — current phase tracker and acceptance-criteria checklist.
- [Plans/](Plans/) — archived plan-mode plans.
- [tests/manual/smoke_test.md](tests/manual/smoke_test.md) — cumulative end-to-end manual playbook, populated phase by phase.

## Tech Stack

- Python 3.11+
- LiteLLM (model routing)
- OpenRouter (model provider)
- SQLite + `sqlite-vec` (canonical storage + semantic recall, in the same DB)
- YAML + Markdown (configuration)
- uv (package management)
- stdlib `asyncio` + a hand-rolled event bus for orchestration

No FastAPI, Redis, Docker, LangGraph, Temporal, Ray, or Kubernetes. `python-telegram-bot` is added in v0.2 when the Telegram channel ships.

## Prerequisites

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/) installed
- An [OpenRouter](https://openrouter.ai/) API key

## Setup

```bash
git clone <repo-url> ubongo
cd ubongo
uv sync
```

Copy the environment template and fill in your secret:

```bash
cp .env.example .env
```

Edit `.env`:

```dotenv
OPENROUTER_API_KEY=sk-or-v1-...
```

Optionally edit `config/UBONGO.md` to customize the global identity context (loaded for every conversation, every persona, every agent — your single source of truth for "who I am and how I want to be talked to"). Optionally edit `config/personas/*.md` to tune voices.

## Run

Two modes, sharing the same SQLite state:

```bash
# REPL (interactive)
uv run python -m ubongo

# One-shot
uv run python -m ubongo send "draft a migration plan"
uv run python -m ubongo send --persona casual --mode parallel "what should I cook tonight"
```

A one-shot continues an ongoing REPL session if you're inside the 30-minute session window. If launching produces nothing, check that `OPENROUTER_API_KEY` is set in `.env` and that `.env` is being loaded.

## Usage

In REPL mode, type messages naturally. Ubongo classifies intent and tone, plans a workflow, runs the agents, gates the result through governance, composes a response in the chosen persona, and writes everything to memory.

Slash commands (REPL only; one-shot uses CLI flags):

The full v0.1 command surface:

### Persona and workflow control

- `/architect`, `/operator`, `/casual` — force a persona for the current session
- `/auto` — return to automatic persona selection
- `/mode <workflow> | list` — pin a workflow (and its execution mode) for the next turn

### Inspection

- `/agents` — list registered worker agents (10: architect, casual, coding, critic, evaluator, execution, memory, operator, repair, research)
- `/skills` — list available skills
- `/decisions [N]` — last N Master Agent decisions for this session (default 10)
- `/trace [N]` — full execution trace for the N most recent turns: classification, workflow agents, per-agent timings + tokens + confidence, repair line, governance decision (default 1)
- `/policy` — print the live governance decision matrix
- `/queue [N]` — outbound queue contents (default 10)
- `/exec <cmd>` — run one command through the constrained-bash sandbox; debug-only, bypasses the workflow runner

### Self-improvement (genetic programming)

- `/optimize <target>` — generate variants for an evolvable target (`persona:*`, `routing:default`, `toolchain:<wf>`, `retry:repair`)
- `/evaluate <target>` — score the latest generation's variants into a fitness leaderboard
- `/evolution <status|pause|resume|off>` — control the autonomous background GP loop (starts paused)
- `/improvements [approve <id> | reject <id> | rollback <target>]` — review and act on proposed promotions (live swap on approve)

### Memory

- `/recall [query]` — recency window + semantic recall (sqlite-vec) + vault-graph neighbors
- `/audit [category] [N]` — tail the unified governance + evolution + sync audit log
- `/conflicts [resolve <id> <keep-mine|keep-theirs|merge>]` — review/resolve external vault-edit collisions

### Skills

- `/skill <name>` — pin a skill for the next turn (one-shot)
- `/summary` — summarize the current conversation (uses the `summarize-conversation` skill)

### System

- `/reload` — hot-reload settings, `UBONGO.md`, personas, skills, and routing
- `/exit` — exit the REPL

Planned (later phases):

- `/mode <workflow>` — force a specific execution mode (Phase 12 brings parallel / competitive / collaborative / debate / speculative)
- `/recall` — what was recalled for the last turn (Phase 20: semantic recall + vault graph)
- `/policy`, `/audit` — governance rules and unified audit log (Phases 14, 21)
- `/optimize <target>`, `/evaluate <target>`, `/improvements`, `/evolution status|pause|resume|off` — GP loop control (Phases 16 through 19)

For one-shot mode, use flags: `--persona <name>`. Skill pinning and mode override are REPL-only today.

## Configuration

| File | Purpose |
| --- | --- |
| `.env` | Secrets only. Never committed. |
| `config/settings.yaml` | Models per agent role, memory tuning, governance thresholds, evolution config, logging. |
| `config/UBONGO.md` | Global identity and communication preferences. Loaded for every persona and every agent. |
| `config/personas/*.md` | Voice-specific overlays for each persona (Architect, Operator, Casual). |
| `config/skills/<name>/SKILL.md` | Skill definitions. Frontmatter + body. |
| `config/routing.yaml` | Tone/intent → workflow mapping rules (evolvable). |
| `config/workflows.yaml` | Named workflow templates: which agents, which execution mode (evolvable). |
| `config/governance.yaml` | Risk rules and decision-matrix thresholds. |
| `config/urgency.yaml` | Urgency assignment rules. v0.3+ scope; empty stub in v0.1. |

Edit any of these and run `/reload` in the REPL to apply without restart (except `settings.yaml`, which requires a restart).

## Vault

Daily conversation logs are written to `vault/daily/YYYY-MM-DD.md` in Obsidian-compatible Markdown. The system audit log lives at `vault/system/audit.md` (governance decisions + evolution promotions / rejections). Open the `vault/` directory as an Obsidian vault to browse.

`vault/` is gitignored by default. If you want versioned history of your conversation logs, run `git init` inside `vault/` separately.

In Phases 5–20 the vault is write-only. Phase 21 enables bidirectional sync: file edits are picked up by a watcher and ingested through the Memory Agent, with conflicts gated by the governance approval flow.

## Project Structure

```text
ubongo/
  config/                          # all user-editable configuration
    UBONGO.md                      # global identity (hierarchical root)
    settings.yaml                  # models, agent budgets, governance, evolution
    routing.yaml                   # tone/intent -> workflow rules
    workflows.yaml                 # named workflow templates (agents, mode, evaluate)
    personas/                      # Architect, Operator, Casual
    skills/
      summarize-conversation/      # Phase 6
      constrained-bash/            # Phase 11 (metadata + prompt; safety in code)
  src/ubongo/
    __main__.py                    # entry: python -m ubongo
    repl.py                        # interactive REPL
    oneshot.py                     # one-shot send command
    master.py                      # Master Agent (orchestrator)
    classifier.py
    router.py                      # workflow + persona resolution from yaml
    runner.py                      # workflow runner (sequential today; Phase 12 adds modes)
    skills.py                      # skill registry with progressive disclosure
    sandbox.py                     # Phase 11: shell-execution safety contract
    context.py
    events.py
    llm.py
    config.py
    logging.py
    agents/
      base.py                      # Agent protocol; AgentInput / AgentResult / AgentDirectives
      llm_run.py                   # shared model-call envelope (run_agent_llm / call_model_or_none)
      personas.py                  # Architect / Operator / Casual subclasses
      research.py                  # cross-conversation + vault retrieval + synthesis
      memory.py                    # single writer for messages + vault + embeddings
      evaluator.py                 # LLM-as-judge: confidence + flagged issues
      critic.py                    # contrarian frame on borderline confidence
      coding.py                    # code-first system prompt + strong coding model
      execution.py                 # bridge to sandbox.run_constrained
      repair.py                    # plan_retry: single-retry with model fallback
    governance/
      decision.py                  # reject-on-low-confidence stub; Phase 14 expands
    delivery/queue.py              # minimal SQLite-backed outbound queue
    memory/                        # SQLite store, schema, compaction, vault projection
  data/ubongo.db                   # canonical SQLite store (gitignored)
  vault/daily/YYYY-MM-DD.md        # projected Markdown daily notes (gitignored)
  Plans/                           # archived plan-mode plans, one per phase
  tests/
    manual/smoke_test.md           # cumulative end-to-end playbook
```

Sub-trees that exist as scaffolding for later phases (`evolution/`, `governance/risk.py`, `governance/approval.py`, `memory/embeddings.py`, `memory/graph.py`) are not in the layout above; they ship in their respective phases. See [UBONGO_BUILD.md](UBONGO_BUILD.md) for the full architecture and the 22-phase plan, and [docs/system-architecture.md](docs/system-architecture.md) for the current implementation diagrams.

## Implementation Workflow

Phases are tracked in [STATUS.md](STATUS.md). Each phase follows the same recipe:

1. Branch off `main` as `phase-N-<short-name>` (names listed in [UBONGO_BUILD.md](UBONGO_BUILD.md) and [STATUS.md](STATUS.md)).
2. Build the sub-phases.
3. Run the per-phase **testing plan** (scenario table) and the cumulative **end-to-end smoke test** in [tests/manual/smoke_test.md](tests/manual/smoke_test.md).
4. When both pass, the user reviews and merges the branch into `main`. No commits to `main` from a phase in progress; no self-merges.
5. Update [STATUS.md](STATUS.md): move the row from "Not started" → "Complete" with a date.
6. Start phase N+1 only after phase N is merged.

Each phase ends with the entire system manually testable end-to-end. New features land additively; old features must not regress.

## Roadmap

**v0.1 (current target).** Full multi-agent runtime + GP-driven self-improvement, accessed through a CLI. Eight worker types, six execution modes, governance layer, sandboxed Execution Agent, continuous evolution with human-approved promotions, semantic memory recall, vault graph. 22 phases on dedicated branches; soft LOC ceiling ~15,000 (excluding tests). See [STATUS.md](STATUS.md) and [UBONGO_BUILD.md](UBONGO_BUILD.md).

**v0.2.** Telegram channel — bring back `python-telegram-bot`, `allowed_user_ids` auth, and the policy engine + quiet hours + holds + catch-up summarizer. The queue and event seams from v0.1 mean Telegram is mostly transport plus a `before_send` policy handler. Plus one or two of: Google Calendar integration, structured fact extraction, bidirectional vault sync polish, a fourth persona.

**v0.3.** Scheduler for proactive jobs (cron-style). Additional integrations as skills (email, news, Reddit). Each integration is a CLI script the Execution Agent invokes through the constrained-bash skill, not a first-class tool definition.

The roadmap is loose. Build v0.1, use it, prioritize v0.2 from observed friction rather than from architectural ambition.

## License

TBD. Personal project; not currently published.
