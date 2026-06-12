<p align="center">
  <img src="docs/Ubongo-Monkey.png" alt="Ubongo logo: a cybernetic monkey with glowing circuitry playing the bongos" width="360">
</p>

# Ubongo

A personal, mood-aware AI mind that runs entirely on your own machine. *Ubongo* is Swahili for *brain*.

> Your own AI. It reads the room, remembers your history, governs its own risk, improves its own prompts, and even writes new skills for itself, and it never does anything irreversible without your say-so.

## Why Ubongo

Cloud assistants are rented, generic, and forgetful. Your conversations live on someone else's servers, the model is the same one everyone else gets, and tomorrow it has forgotten today. Ubongo is the opposite:

- **Yours and local.** It runs on hardware you own (a Raspberry Pi is enough). Your conversations live in a local SQLite database and an Obsidian vault you control. No account, no cloud lock-in.
- **Mood-aware.** It reads the tone of each message and answers in the right voice: deep and architectural, terse and operational, or warm and casual.
- **It remembers.** Every turn is persisted and semantically searchable. Ask "what did we decide about caching last week" and the relevant old turns come back, even after a restart.
- **It improves itself, with you in control.** A background loop evolves its own prompts and routing and surfaces the winners; nothing changes until you approve it.
- **It extends itself, with you in control.** It can draft brand-new skills for things you keep asking for; nothing becomes usable until you approve it.
- **It is governed.** Every turn is scored for risk and reversibility. Anything destructive stops and asks first, and shell access is locked inside a sandbox.

## What It Is

Three things in one package, hand-rolled in plain Python (no LangGraph, no Temporal, no Docker):

- **A multi-agent orchestrator.** A Master Agent classifies each turn, plans a workflow, dispatches a fleet of ten worker agents (three persona voices plus Research, Coding, Evaluator, Critic, Execution, Memory, Repair), and composes the response across six execution modes: sequential, parallel, competitive, collaborative, debate, speculative.
- **A self-improving, self-extending runtime.** A continuous Genetic Programming loop evolves prompts, routing rules, tool chains, and retry strategies; a separate authoring loop drafts entirely new skills. Both run sandboxed, are fully traced, and promote nothing without your approval.
- **A memory-centric local system.** SQLite is canonical; an Obsidian-compatible Markdown vault is projected from it; `sqlite-vec` indexes messages and notes for semantic recall; vault links form a graph the agents can traverse.

Single user, single machine, accessed through a CLI (REPL primary, one-shot for scripting).

## What It Does

- **Talks in three personas** and switches automatically by reading your tone, or on command (`/architect`, `/operator`, `/casual`).
- **Runs a real agent fleet** per turn: research over your own history, code generation, an evaluator that scores confidence, a contrarian critic, sandboxed shell execution, an MCP connector for external services, and a repair agent that recovers failures on its own.
- **Gates risk.** A destructive request like "delete the entire vault" triggers an `Approve? (y/n/why)` prompt; shell commands run in a locked-down sandbox (allowlist, no network, repo-root, 10s timeout).
- **Improves its own prompts** (`/optimize`, `/evaluate`, `/improvements`) and **authors its own skills** (`/author`, `/skill-candidates`), both behind your explicit approval.
- **Remembers and recalls** across restarts: recency plus semantic search (`/recall`), a browsable Obsidian journal, and bidirectional vault sync.
- **Traces everything.** Every decision, agent run, governance call, repair, and evolution variant is persisted and auditable (`/trace`, `/decisions`, `/audit`).
- **Speaks MCP in both directions.** As a server (`ubongo mcp`), external agents (Claude Code, Compendium) can run a full governed turn or read memory ([ADR-0015](docs/adr/0015-mcp-server-additive-channel.md)); as a client, the Connector agent calls the MCP servers you declare in config — `/mode connector_session` — with governance escalating risk per server ([ADR-0016](docs/adr/0016-connector-agent-external-tools-one-seam.md)).
- **Profiles itself, locally.** On-demand performance breakdowns by agent/model/mode, opt-in CPU (cProfile) and memory (tracemalloc) profiling (`/profile`, `--profile`, `UBONGO_PROFILE`); nothing telemetric ever leaves the machine ([ADR-0014](docs/adr/0014-local-only-observability-profiler.md)).

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

Three background daemon threads run alongside that turn loop, all paused or off by default: the **Genetic Programming loop** (evolve a target, evaluate it in a sandbox against held-out samples, surface winners via `/improvements`), the **vault watcher** (ingest edits you make in Obsidian), and the **authoring daemon** (draft brand-new skills into quarantine for you to review). None of them changes anything live without your approval.

For the full picture: a turn [flow + UML sequence diagram](docs/architecture/flow-and-sequence.md), the [agent fleet diagrams](docs/architecture/agents.md), and the [C4 architecture set](docs/architecture/).

## Status

**Current version: v0.1.4.** v0.1 (the 22-phase build) is complete, plus four post-v0.1 layers: the optional web UI (v0.1.1), self-authored skills (v0.1.2), the local profiler + service control (v0.1.3), and the MCP server channel (v0.1.4). The v0.2 milestone is Telegram.

**v0.1 is complete: all 22 phases (0–21) are merged to `main` and certified, and a post-v0.1 self-extension layer (self-authored skills) ships on top.** The CLI runs end to end: classify, plan, execute through the worker fleet, govern, compose, enqueue, persist. Ten worker agents are registered; all six execution modes are live; the Repair Agent walks a full recovery ladder; the governance decision matrix gates risky turns through an interactive `y/n/why` approval flow over a hardened sandbox. The Genetic Programming loop is closed: generate variants of persona prompts *and* routing / tool-chain / retry config, evaluate against held-out samples, evolve generations, and propose promotions that live-swap only after you approve them. Semantic recall (`sqlite-vec`) augments recency, a vault-link graph is queryable, and a polling watcher ingests your vault edits. Post-v0.1, Ubongo also drafts brand-new skills behind the same approval boundary (`/author`, `/skill-candidates`, and an autonomous authoring daemon) and profiles itself locally (`/profile`; [ADR-0014](docs/adr/0014-local-only-observability-profiler.md)). **929 tests green; ~14,700 LOC**; the full cumulative smoke passes end to end (last re-certified 2026-06-11, with the profiler armed).

The v0.1 build ran across **22 phases in 6 tiers**, each on its own branch and smoke-tested before merge; the self-extension work added five more phases the same way. See [STATUS.md](STATUS.md) for the changelog, [STATE.md](STATE.md) for ground-truth state, and [UBONGO_BUILD.md](UBONGO_BUILD.md) for the v0.1 spec. Next is **v0.2 (Telegram)**, a new transport that is additive on the existing event/queue seams.

## What Ubongo Is Not

- A multi-channel system. The CLI is the primary channel; an optional self-hosted web UI shipped post-v0.1 for trusted-LAN use. Telegram is the v0.2 milestone; Slack/WhatsApp/Discord/voice are not on the roadmap.
- A production system or SaaS product.
- A multi-user or team tool.
- A distributed system. Single process, single machine.

## Documentation Map

- [README.md](README.md) — this file. Goal, setup, usage, roadmap, contributing.
- [docs/system-architecture.md](docs/system-architecture.md) — current implementation architecture with Mermaid diagrams (runtime flow, events, data model).
- [docs/architecture/](docs/architecture/) — C4 diagrams (context, container, component, dynamic-turn) with reading order.
- [docs/adr/](docs/adr/) — architecture decision records (the load-bearing decisions, ADR-0001 … ADR-0013).
- [CONTEXT.md](CONTEXT.md) — the domain glossary (canonical terms + words to avoid).
- [docs/SECURITY.md](docs/SECURITY.md) — the v0.1 security model: governance gate, execution sandbox, self-authored skills, optional web UI.
- [docs/USER_MANUAL.md](docs/USER_MANUAL.md) — end-user guide (install, commands, day-to-day use).
- [UBONGO_BUILD.md](UBONGO_BUILD.md) — full v0.1 build specification, 22 phases with sub-phases and per-phase testing plans. Source of truth.
- [UBONGO_VISION.md](UBONGO_VISION.md) — design exposition the v0.1 build realizes.
- [CLAUDE.md](CLAUDE.md) — context for Claude Code sessions.
- [VERSION](VERSION) — the current version, single line (the source of truth for the version number).
- [CHANGELOG.md](CHANGELOG.md) — the versioning scheme (v0.MAJOR.PHASE) and what each version added.
- [STATUS.md](STATUS.md) — current phase tracker and acceptance-criteria checklist (incl. post-v0.1 work).
- [STATE.md](STATE.md) — ground-truth state: what's built, drift from spec, decisions, what's parked.
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
- [uv](https://docs.astral.sh/uv/) installed (from-source setup only — the release installer needs just Python)
- An [OpenRouter](https://openrouter.ai/) API key

## Download & Install (from a Release)

The distribution lives on the GitHub Releases page:
**<https://github.com/GTuritto/ubongo/releases>** (latest:
[github.com/GTuritto/ubongo/releases/latest](https://github.com/GTuritto/ubongo/releases/latest)).
Every release is published automatically by the pipeline once all gates are
green, and carries exactly two assets: the bootstrap installer
(`install-ubongo.sh`) and the bundle (`ubongo-v<version>.zip`).

On the target machine (macOS or Linux, including a Raspberry Pi — only
Python 3.11+ required, no git/uv):

```bash
# 1. Download both assets into the same directory
curl -LO https://github.com/GTuritto/ubongo/releases/latest/download/install-ubongo.sh
curl -LO "$(curl -s https://api.github.com/repos/GTuritto/ubongo/releases/latest \
  | grep browser_download_url | grep -o 'https://[^"]*\.zip')"
# (or just download both files from the Releases page in a browser)

# 2. Run the installer — it unpacks the bundle, installs dependencies into a
#    private venv, and asks for your OpenRouter API key and install location
chmod +x install-ubongo.sh
./install-ubongo.sh                  # add --web for the tablet UI, --dest DIR to skip the prompt

# 3. Start it (the bundle unpacks into a versioned folder under your --dest)
cd ~/ubongo/ubongo-v0.1.3            # the installer prints the exact path
./start-ubongo.sh                    # REPL
./start-ubongo-web.sh                # web UI (if installed with --web)
./ubongo-ctl.sh start                # web UI as a background service
```

Upgrades are side-by-side: installing a newer release unpacks
`ubongo-v<new>/` next to the old folder, which stays untouched. Your state
(`data/`, `vault/`, `.env`) lives inside each versioned folder — copy those
three into the new folder to migrate, then retire the old one. Full first-run
guidance lives in [docs/USER_MANUAL.md](docs/USER_MANUAL.md).

## Setup (from source)

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
uv run python -m ubongo send "what should I cook tonight" --persona casual
```

A one-shot continues an ongoing REPL session if you're inside the 30-minute session window. If launching produces nothing, check that `OPENROUTER_API_KEY` is set in `.env` and that `.env` is being loaded.

### Web UI (optional, self-hosted)

A local Streamlit chat page for driving Ubongo from another device on your home
network (e.g. a tablet). It is an additive channel — it calls the same
`master.handle` seam as the REPL, so classify → plan → execute → govern → compose
→ enqueue all run unchanged; the governance approval gate becomes Approve/Deny
buttons. Streamlit is an optional dependency, kept out of the core.

```bash
uv sync --extra web          # install Streamlit once
./start-ubongo-web.sh         # binds 0.0.0.0:8501; UBONGO_WEB_PORT to override
# then open http://<this-machine-ip>:8501 on your tablet
```

**Security:** no auth and no TLS, by design — intended for a trusted home LAN
only. Anyone who can reach the page can drive the agent. Do not port-forward it or
expose it to the internet.

### Service control + startup profiling

For a long-running web deployment (e.g. the Pi):

```bash
./ubongo-ctl.sh start|stop|restart|status    # background the web UI (pidfile + log under data/)
# or, for reboot survival on the Pi: deploy/ubongo-web.service (install steps in its comments)
UBONGO_PROFILE=cpu ./start-ubongo.sh          # start a session with the profiler armed (cpu | mem | all)
./start-ubongo.sh --profile mem               # same via flag; --profile off overrides the env
```

### Connect via MCP (other agents calling Ubongo)

With the optional extra installed (`./install.sh --mcp` or `uv sync --extra mcp`),
Ubongo is an MCP server exposing `ubongo_send` (a full governed turn),
`ubongo_recall` (read-only memory), and two read-only resources. A turn the
governance gate holds returns `gated: true` and cannot be approved over MCP.

For a local client that spawns its own server (e.g. Claude Code on the same
machine), register the stdio form:

```json
{
  "mcpServers": {
    "ubongo": {
      "command": "/path/to/ubongo/.venv/bin/python",
      "args": ["-m", "ubongo", "mcp"]
    }
  }
}
```

For services on your LAN (e.g. Compendium on another box), serve streamable
HTTP and point the client at `http://<this-host>:8765/mcp`:

```bash
./start-ubongo-mcp.sh              # foreground
./ubongo-ctl.sh start mcp          # background service (stop|restart|status mcp)
# Pi/Ubuntu reboot-survival: deploy/ubongo-mcp.service
```

Same security posture as the web UI: no auth, no TLS, home LAN only
([docs/SECURITY.md](docs/SECURITY.md)).

**The other direction** — Ubongo calling external MCP servers (v0.1.5): declare
them in `config/settings.yaml` and invoke the Connector explicitly:

```yaml
mcp:
  servers:
    compendium:
      transport: http
      url: http://192.168.1.50:9000/mcp
      risk: low        # governance escalates the turn to at least this level
      enabled: true
```

```text
> /mode connector_session
> what does Compendium know about X?
```

The Connector plans and executes the tool calls and threads the results to the
architect; a dead server degrades to a normal answer. Not auto-routed by
design; tokens for protected servers come from `.env` via each server's
`env:` map, never from config.

Deployment bundles are published automatically: merging a `VERSION` bump to
`main` makes the release pipeline run the tests **and the automated smoke gate**
(`scripts/smoke.sh`; plus a small live-model subset when an API-key secret is
configured), build the bundle (`scripts/package.sh`), and publish a GitHub
Release `v<VERSION>` with `install-ubongo.sh` + the zip attached. The release
is created only when every gate is green. To deploy, download both assets on the
target and run `./install-ubongo.sh`. (CI also builds the bundle on every PR as
a workflow artifact.)

## Usage

In REPL mode, type messages naturally. Ubongo classifies intent and tone, plans a workflow, runs the agents, gates the result through governance, composes a response in the chosen persona, and writes everything to memory.

Slash commands (REPL only; one-shot uses CLI flags):

The full v0.1 command surface:

### Persona and workflow control

- `/architect`, `/operator`, `/casual` — force a persona for the current session
- `/auto` — return to automatic persona selection
- `/mode <workflow> | list` — pin a workflow (and its execution mode) for the next turn

### Inspection

- `/agents` — list registered worker agents (11: architect, casual, coding, connector, critic, evaluator, execution, memory, operator, repair, research)
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

### Self-extension (authored skills, post-v0.1)

Beyond tuning existing prompts/config, Ubongo can author brand-new skills behind a human approval boundary ([ADR-0013](docs/adr/0013-self-authored-skills-quarantine-and-approval.md)). Drafts are quarantined (invisible to the runtime) until you approve them.

- `/author <description>` — draft a new skill from a capability description; it is validated, quarantined, and given an estimated quality score
- `/skill-candidates [approve <id> | reject <id> | rollback <name>]` — review drafts and act: approve materializes the skill into the live registry (backing up any prior version), rollback restores the prior version or unregisters
- `/authoring <status|pause|resume|off>` — control the autonomous authoring daemon: it boots paused, is throttled, infers recurring capability gaps, and drafts into quarantine (approval stays manual)

### Memory

- `/recall [query]` — recency window + semantic recall (sqlite-vec) + vault-graph neighbors
- `/audit [category] [N]` — tail the unified governance + evolution + sync audit log
- `/conflicts [resolve <id> <keep-mine|keep-theirs|merge>]` — review/resolve external vault-edit collisions

### Diagnostics (local profiler)

- `/profile [N]` — summary over the run history: turns, avg + p95 latency, tokens, slowest agent
- `/profile agents|models|modes [N]` — breakdowns by agent / model / execution mode
- `/profile cpu on|off|status` — arm cProfile around each turn (`.prof` under `data/profiles/` + a top-25 summary); also `ubongo send --profile`
- `/profile mem [on|off|status]` — tracemalloc baseline on arm; bare `/profile mem` reports allocation growth since the baseline

### Skills

- `/skill <name>` — pin a skill for the next turn (one-shot)
- `/summary` — summarize the current conversation (uses the `summarize-conversation` skill)

### System

- `/reload` — hot-reload settings, `UBONGO.md`, personas, skills, and routing
- `/exit` — exit the REPL

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
    profiling.py                   # local profiler: stats over run tables + opt-in cProfile/tracemalloc (v0.1.3)
    mcp/                           # MCP server channel: service.py (core) + server.py (SDK adapter) (v0.1.4)
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

The layout above is an early-v0.1 snapshot. Several sub-trees shown there as stubs are now fully built (the whole `evolution/` GP package, `governance/{risk,confidence,reversibility,approval}.py`, `memory/{embeddings,graph,vault_watch}.py`), and post-v0.1 additions landed: `src/ubongo/web/` (the optional Streamlit channel, v0.1.1), `src/ubongo/authoring/` — self-authored skills behind a human approval gate ([ADR-0013](docs/adr/0013-self-authored-skills-quarantine-and-approval.md), v0.1.2) — and `src/ubongo/profiling.py` plus `ubongo-ctl.sh`/`deploy/` — the local profiler and web service control ([ADR-0014](docs/adr/0014-local-only-observability-profiler.md), v0.1.3). For the current module map see [docs/architecture/](docs/architecture/) (the C4 diagrams) and [docs/system-architecture.md](docs/system-architecture.md); [UBONGO_BUILD.md](UBONGO_BUILD.md) remains the v0.1 build spec.

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
