# Ubongo — Build Specification (v0.1: Multi-Agent + Self-Improving, CLI-First)

This document is the build spec for Ubongo v0.1. Phased releases with explicit acceptance criteria. v0.1 ships a working personal AI mind: a hand-rolled multi-agent runtime with continuous, GP-driven self-improvement, accessed through a local CLI. Telegram remains v0.2.

Build phase by phase. Each phase ships a system that's manually testable end-to-end. Don't move to phase N+1 until N's testing plan and smoke test pass.

**Workflow rule:** every phase is implemented on a dedicated git branch (`phase-N-<short-name>`). No commits to `main` from a phase in progress. The user reviews and merges the branch into `main` after the phase's testing plan and smoke test pass.

## What Ubongo Is

A personal, mood-aware AI mind for one user (Giuseppe), running locally as a CLI. It is **a multi-agent orchestration platform** and **a self-improving runtime** in the same package. Inside, a **Master Agent** orchestrates a fleet of disposable **worker agents** (Research, Coding, Evaluator, Repair, Memory, Critic, Execution, Persona) across six execution modes (sequential, parallel, competitive, collaborative, debate, speculative). A **governance layer** evaluates risk, confidence, and reversibility per turn and gates risky actions through human approval. A **continuous self-improvement loop** uses Genetic Programming over prompts, routing rules, tool chains, and retry strategies — generations run autonomously, evaluation is sandboxed, promotions require user approval. Memory is SQLite-canonical with an Obsidian-compatible Markdown vault projection; embeddings (sqlite-vec) and graph relationships ride alongside.

The CLI is the v0.1 channel: REPL primary, one-shot for scripting. Telegram comes in v0.2.

## What Ubongo Is Not (v0.1)

- A multi-channel system. v0.1 is CLI-only. Telegram is deferred to v0.2; Slack, WhatsApp, Discord, web UI, voice are not on the roadmap.
- A production system or SaaS product.
- A multi-user or team tool.
- A distributed system. Single process, single machine.

If a feature is not explicitly listed in the phased build plan below, it is out of scope for v0.1.

## Core Design Decisions

1. **Single channel: CLI.** REPL plus one-shot. Telegram is v0.2.
2. **Master Agent orchestrates.** Not a router-then-LLM sequence; a real orchestrator that classifies, plans a workflow, dispatches workers, gates output, and composes the response.
3. **Worker agents are disposable.** Spawn per turn (or per workflow), execute, return results, dissolve. Durable intelligence lives in memory, workflows, policies, and the evolution lineage — not in agents.
4. **Hand-rolled orchestration.** Pure Python: classes, `asyncio` for parallel, an event bus for decoupling. No LangGraph, no Temporal, no Ray.
5. **Personas are voice; skills are capability; agents are role.** A Persona Agent wears a voice (Architect / Operator / Casual). A worker agent does a job (Research, Critic, etc.). A skill is a progressive-disclosure unit of capability that any agent can invoke.
6. **Hierarchical context.** System prompts are assembled per turn from `UBONGO.md` (global) + active persona + active skill body + worker-specific frame. Closer-to-task layers come last.
7. **SQLite canonical, Markdown projected, embeddings indexed, graph linked.** Conversation logs and structured facts live in SQLite. Daily-note Markdown projects out of SQLite for human readability. `sqlite-vec` indexes messages and vault notes for semantic recall. Vault-note links create a graph; the Master Agent can traverse it.
8. **Models via OpenRouter through LiteLLM.** Different models for different agents and decisions: small fast model for classification, strong model for architect, cheap model for casual, etc.
9. **All outbound messages flow through the queue.** Even synchronous CLI responses. The seam supports proactive output (v0.3) and Telegram delivery (v0.2) without restructuring.
10. **Governance is first-class.** Decision matrix evaluates Intent + Risk + Confidence + Context + Preferences + Reversibility per turn. High-risk actions gate through user approval.
11. **Self-improvement is continuous and approved.** GP loop runs in the background, generates and evaluates variants, persists lineage. Promotions to production require user approval via `/improvements`.
12. **Named events** are the extension surface. `before_classify`, `after_classify`, `before_plan`, `after_plan`, `before_execute`, `after_execute`, `before_govern`, `after_govern`, `before_compose`, `after_compose`, `before_send`, `after_send`, plus per-agent `agent_started`, `agent_completed`, `agent_failed`. Future behavior plugs in as event handlers.
13. **Configuration in YAML, secrets in `.env`.** Code reads config; config never contains secrets.
14. **Local-first, single-user.** No auth in CLI. When Telegram lands in v0.2, the `allowed_user_ids` allowlist comes back.
15. **Per-phase branches.** Implementation phases (0 through 21) each get a dedicated git branch; user reviews and merges to `main` only after the phase's testing plan and smoke test pass.

## Tech Stack

| Layer | Technology | Why |
| --- | --- | --- |
| Language | Python 3.11+ | Ecosystem, asyncio, fast iteration |
| LLM routing | LiteLLM | Provider abstraction without lock-in |
| Model provider | OpenRouter | Single key, every model, easy A/B |
| Storage | SQLite (stdlib) | Single-user, zero-ops |
| Vector index | `sqlite-vec` | Semantic recall over messages and vault, in the same DB |
| Config | YAML + Markdown | Editable without touching code |
| Secrets | python-dotenv | Standard `.env` loading |
| Tests | pytest | Standard |
| Package management | uv | Fast, modern |
| CLI parsing | stdlib argparse | No new dep |

No FastAPI, no Redis, no Docker, no LangGraph, no Temporal, no Ray, no Kubernetes. `python-telegram-bot` is added in v0.2 when the Telegram channel ships.

## Architecture

### The Core Loop

```text
CLI input (REPL stdin or argv)
    |
    v   [event: before_classify]
Master Agent.classify(message, context)
    |   -> Classification(intent, tone, task_type, suggested_skill, risk, confidence)
    v   [event: after_classify]
Master Agent.plan(classification, context)
    |   -> Workflow(agents, execution_mode, persona, model_overrides)
    v   [event: before_execute]
Workflow Runner.execute(workflow, context)
    |   -> spawn agents per mode (sequential/parallel/etc.)
    |   -> route messages between agents
    |   -> aggregate results (Evaluator picks/merges)
    v   [event: after_execute]
Governance.gate(workflow_result, classification, context)
    |   -> Action(auto / ask_clarification / require_approval / reject)
    v   [event: before_compose]
Persona Agent.compose(workflow_result, persona, history)
    |   -> final user-facing text
    v   [event: after_compose]
enqueue(content, urgency='urgent', source='response')
    |
    v   [event: before_send]
dequeue + print to stdout
    |   [event: after_send]
    v
Memory Agent.write(turn) -> SQLite + vault + embeddings
```

For proactive messages (v0.3+) the flow starts at `enqueue`. The same workflow runner, governance gate, and persona composer apply. The GP loop runs in parallel, on its own asyncio task, hitting OpenRouter at low priority.

### The Master Agent

`src/ubongo/master.py`. Methods:

- `classify(message, context) -> Classification`. Single LLM call to the classifier model. JSON output with intent, tone, task_type, suggested_skill, risk, confidence. Falls back to default classification on parse failure.
- `plan(classification, context) -> Workflow`. Reads `routing.yaml` and `workflows.yaml`. Picks agents (which workers + which persona), execution mode, model overrides. Returns a `Workflow` object.
- `execute(workflow, context) -> WorkflowResult`. Delegates to the Workflow Runner.
- `decide(...) -> Decision`. Implements the decision matrix. v0.1 default: `auto` for everything until Phase 14 lands the rules.
- `handle(message) -> Response`. End-to-end orchestration: classify → plan → execute → governance → compose → enqueue → memory. This is what the REPL/oneshot calls.

### Worker Agents

Each implements the `Agent` protocol (`src/ubongo/agents/base.py`):

```python
class Agent(Protocol):
    name: str
    role: str
    default_model: str

    async def run(self, input: AgentInput, context: Context) -> AgentResult: ...
```

Workers are disposable: instantiated for a workflow, run once, return a `AgentResult`, dissolve. State that needs to persist goes through the Memory Agent.

| Worker | Purpose | v0.1 backend |
| --- | --- | --- |
| Research Agent | Retrieval + synthesis | LLM-only retrieval over conversation memory and vault. Web/calendar/email skills are v0.2+. |
| Coding Agent | Code generation, refactoring, review | Strong coding model. |
| Evaluator Agent | Validates output; produces confidence score | LLM-as-judge with criteria: correctness, completeness, hallucination signals. |
| Repair Agent | Detects and recovers failed workflows | Phase 13 logic: retry with different model, replace stuck agent, rollback. |
| Memory Agent | Single writer to durable memory | All persistence flows through here: messages, summaries, facts, vault, embeddings, lineage. |
| Critic Agent | Contrarian / brutal analysis | Argues against the prevailing answer. Used in debate mode and as a Master-Agent-summoned challenger when confidence is borderline. |
| Execution Agent | Runs shell scripts and external APIs | Constrained-bash skill in Phase 11; sandboxed in Phase 15. |
| Persona Agents | Voice for the user-facing surface | Architect / Operator / Casual; assemble the final response from worker output. |

### Execution Modes

`src/ubongo/runner.py` implements all six:

1. **Sequential.** A → B → C. Default for simple workflows.
2. **Parallel.** A | B | C, results aggregated by Evaluator. `asyncio.gather`.
3. **Competitive.** Same input, multiple agents, Evaluator picks the best output.
4. **Collaborative.** Each agent owns a subtask; results merged structurally (e.g., Research handles facts, Critic handles risks, both merged into a brief).
5. **Debate.** Two agents argue opposing positions for N rounds; Critic synthesizes the resolution.
6. **Speculative.** Cheap fast agent runs first; strong agent validates in the background. User sees the cheap result; if validation contradicts, the system corrects within the same session via a follow-up message.

Mode selection lives in `routing.yaml` and is overridable per workflow type. The GP loop can evolve the mode-selection rules.

### Governance Layer

`src/ubongo/governance/`.

- **Risk evaluation** (`risk.py`): per-workflow risk tag (`low`, `medium`, `high`, `destructive`). Initial rules read from `governance.yaml`.
- **Confidence** (`confidence.py`): from the Evaluator Agent's score. Threshold-based.
- **Reversibility** (`reversibility.py`): declared per skill in its frontmatter (`reversibility: reversible | irreversible`).
- **Decision matrix** (`decision.py`): given Intent, Risk, Confidence, Context, Preferences, Reversibility → Action ∈ {auto, ask_clarification, require_approval, reject}.
- **Approval** (`approval.py`): for `require_approval`, the CLI prompts the user with the proposed action and a one-line rationale; user types `yes` / `no` / `why` to confirm, reject, or expand.

### Self-Improvement (GP Loop)

`src/ubongo/evolution/`.

- **Generation** (`generator.py`): given a target (prompt / routing rule / tool chain / retry strategy), emit N variants. Variant strategies: paraphrase, prune, expand, recombine, perturb-temperature.
- **Sandbox evaluation** (`sandbox.py`): run a candidate against the held-out conversation sample. Sample is curated and anonymized from prior sessions. Each candidate produces a `EvaluationResult` (per-sample scores).
- **Fitness** (`fitness.py`): weighted sum of normalized success_rate, cost_inverse, latency_inverse, hallucination_inverse, user_correction_inverse. Weights live in `settings.yaml` and can themselves be evolved.
- **Selection + lineage** (`selection.py`, `lineage.py`): top variants survive. `evolution_lineage` table records parent → child edges, fitness scores, and timestamps.
- **Promotion** (`promotion.py`): top candidates are queued in `pending_promotions`; user approves via `/improvements`. Approved variants replace the live target; old version is preserved and reachable.
- **Loop** (`loop.py`): asyncio task runs at low priority, throttled by `evolution.max_calls_per_hour` in `settings.yaml`. Triggered manually with `/evolve <target>` or scheduled by `evolution.cron`.

## File Structure

```text
ubongo/
  pyproject.toml
  README.md
  CLAUDE.md
  STATUS.md
  UBONGO_VISION.md
  UBONGO_BUILD.md            # this file
  .env.example
  .gitignore
  Plans/                     # archived plan-mode plans

  config/
    UBONGO.md                # global identity (hierarchical root)
    settings.yaml
    routing.yaml             # tone/intent -> workflow rules
    workflows.yaml           # named workflow templates (which agents, which mode)
    governance.yaml          # risk rules, decision-matrix thresholds
    urgency.yaml             # urgency assignment (used in v0.3)
    personas/
      architect.md
      operator.md
      casual.md
    skills/
      summarize-conversation/
        SKILL.md
        prompts/summarize.md
      constrained-bash/
        SKILL.md
        prompts/run.md

  src/ubongo/
    __init__.py
    __main__.py              # entry: python -m ubongo
    repl.py                  # interactive REPL
    oneshot.py               # one-shot send command
    config.py
    context.py
    logging.py
    events.py                # named-event dispatcher
    llm.py                   # LiteLLM wrapper
    master.py                # Master Agent
    classifier.py
    router.py                # internal helper used by Master Agent.plan
    runner.py                # workflow runner (six modes)
    composer.py              # Persona Agent / response composition
    skills.py                # skill registry, progressive disclosure

    agents/
      __init__.py
      base.py                # Agent protocol, AgentInput/Result
      research.py
      coding.py
      evaluator.py
      repair.py
      memory.py
      critic.py
      execution.py
      personas.py            # Architect, Operator, Casual

    governance/
      __init__.py
      risk.py
      confidence.py
      reversibility.py
      decision.py
      approval.py

    evolution/
      __init__.py
      generator.py
      sandbox.py
      fitness.py
      selection.py
      lineage.py
      promotion.py
      loop.py

    delivery/
      __init__.py
      queue.py               # minimal SQLite-backed outbound queue

    memory/
      __init__.py
      schema.sql
      store.py               # SQLite operations
      compaction.py          # swappable compaction
      vault.py               # Markdown projection
      embeddings.py          # sqlite-vec wrapper
      graph.py               # vault-link graph

  tests/
    __init__.py
    conftest.py
    test_classifier.py
    test_master.py
    test_runner.py
    test_agents_research.py
    test_agents_evaluator.py
    test_governance_decision.py
    test_evolution_generator.py
    test_evolution_fitness.py
    test_evolution_lineage.py
    test_memory_store.py
    test_memory_compaction.py
    test_memory_embeddings.py
    test_delivery_queue.py
    test_skills.py
    test_events.py

    manual/
      smoke_test.md          # cumulative manual playbook
      fixtures/
        sample_conversations.json   # held-out eval set for GP

  vault/                     # gitignored; daily notes + ingested user pages
```

## Hierarchical Context Loader

`src/ubongo/context.py`. Function `build_system_prompt(persona_name, skill_name=None, agent_role=None) -> str`:

1. Read `config/UBONGO.md` body (cached).
2. Read `config/personas/<persona>.md` body (skipping frontmatter).
3. If `skill_name` is provided, read the skill body and prefix with `## Active Skill: <name>`.
4. If `agent_role` is provided, append a short role-frame stanza describing the agent's posture.
5. Concatenate with double newlines.

Caching: `UBONGO.md` and persona files at startup and on `/reload`. Skill bodies on demand, cached per skill, cleared on `/reload`. Worker role frames at startup.

## Named Events

`src/ubongo/events.py`. Synchronous-by-default dispatcher. v0.1 events:

| Event | Payload | Default handler |
| --- | --- | --- |
| `before_classify` | `{message, session}` | passthrough |
| `after_classify` | `{message, session, classification}` | passthrough |
| `before_plan` | `{classification, session}` | passthrough |
| `after_plan` | `{workflow}` | passthrough |
| `before_execute` | `{workflow, context}` | passthrough |
| `after_execute` | `{workflow_result}` | passthrough |
| `before_govern` | `{workflow_result, classification}` | passthrough |
| `after_govern` | `{decision, action}` | passthrough |
| `before_compose` | `{workflow_result, persona}` | passthrough |
| `after_compose` | `{response, persona}` | passthrough |
| `before_send` | `{queue_item, now}` | passthrough (policy engine in v0.2) |
| `after_send` | `{queue_item, output_id}` | vault projection, memory write |
| `agent_started` | `{agent, input}` | log |
| `agent_completed` | `{agent, result}` | log |
| `agent_failed` | `{agent, exception}` | log + repair trigger (Phase 13) |
| `evolution_generation` | `{target, generation, candidates}` | persist to lineage |
| `evolution_promotion` | `{target, candidate, fitness}` | queue for `/improvements` |

## Memory Model

SQLite schema (`memory/schema.sql`):

```sql
CREATE TABLE conversations (
  id INTEGER PRIMARY KEY,
  started_at TIMESTAMP NOT NULL,
  ended_at TIMESTAMP,
  active_persona TEXT
);

CREATE TABLE messages (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id),
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  timestamp TIMESTAMP NOT NULL,
  persona TEXT, agent TEXT, skill TEXT, model TEXT,
  tokens_in INTEGER, tokens_out INTEGER
);

CREATE TABLE summaries (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL,
  covers_from_message_id INTEGER NOT NULL,
  covers_to_message_id INTEGER NOT NULL,
  content TEXT NOT NULL,
  strategy TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL
);

CREATE TABLE sessions (
  user_id INTEGER PRIMARY KEY,
  last_message_at TIMESTAMP,
  active_persona TEXT,
  override_until TIMESTAMP,
  current_conversation_id INTEGER REFERENCES conversations(id)
);

CREATE TABLE facts (
  id INTEGER PRIMARY KEY,
  subject TEXT, predicate TEXT, object TEXT,
  source_message_id INTEGER REFERENCES messages(id),
  importance INTEGER DEFAULT 0,
  created_at TIMESTAMP NOT NULL
);

CREATE TABLE workflow_runs (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  classification JSON NOT NULL,
  workflow JSON NOT NULL,
  execution_mode TEXT NOT NULL,
  started_at TIMESTAMP NOT NULL,
  ended_at TIMESTAMP,
  outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failure', 'repaired'))
);

CREATE TABLE agent_runs (
  id INTEGER PRIMARY KEY,
  workflow_run_id INTEGER NOT NULL REFERENCES workflow_runs(id),
  agent TEXT NOT NULL,
  model TEXT,
  input JSON, output JSON,
  confidence REAL,
  tokens_in INTEGER, tokens_out INTEGER, latency_ms INTEGER,
  outcome TEXT NOT NULL,
  started_at TIMESTAMP NOT NULL,
  ended_at TIMESTAMP
);

CREATE TABLE governance_decisions (
  id INTEGER PRIMARY KEY,
  workflow_run_id INTEGER NOT NULL REFERENCES workflow_runs(id),
  intent TEXT, risk TEXT, confidence REAL, reversibility TEXT,
  action TEXT NOT NULL,
  approval_response TEXT,
  decided_at TIMESTAMP NOT NULL
);

CREATE TABLE evolution_lineage (
  id INTEGER PRIMARY KEY,
  target TEXT NOT NULL,
  parent_id INTEGER REFERENCES evolution_lineage(id),
  generation INTEGER NOT NULL,
  variant_text TEXT NOT NULL,
  variant_metadata JSON,
  created_at TIMESTAMP NOT NULL
);

CREATE TABLE evolution_evaluations (
  id INTEGER PRIMARY KEY,
  lineage_id INTEGER NOT NULL REFERENCES evolution_lineage(id),
  sample_set TEXT NOT NULL,
  success_rate REAL, cost REAL, latency_ms REAL,
  hallucination_rate REAL, user_correction_rate REAL,
  fitness REAL NOT NULL,
  evaluated_at TIMESTAMP NOT NULL
);

CREATE TABLE pending_promotions (
  id INTEGER PRIMARY KEY,
  lineage_id INTEGER NOT NULL REFERENCES evolution_lineage(id),
  target TEXT NOT NULL,
  proposed_at TIMESTAMP NOT NULL,
  decided_at TIMESTAMP,
  decision TEXT CHECK (decision IN ('approved', 'rejected'))
);

CREATE TABLE active_evolutions (
  target TEXT PRIMARY KEY,
  lineage_id INTEGER NOT NULL REFERENCES evolution_lineage(id),
  promoted_at TIMESTAMP NOT NULL
);

CREATE TABLE notification_queue (
  id INTEGER PRIMARY KEY,
  content TEXT NOT NULL,
  urgency TEXT NOT NULL CHECK (urgency IN ('low', 'normal', 'urgent')),
  source TEXT,
  created_at TIMESTAMP NOT NULL,
  deliver_after TIMESTAMP,
  delivered_at TIMESTAMP,
  expires_at TIMESTAMP,
  metadata JSON
);

CREATE TABLE vault_links (
  source_path TEXT NOT NULL,
  target_path TEXT NOT NULL,
  link_type TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL,
  PRIMARY KEY (source_path, target_path, link_type)
);

-- vec_messages and vec_vault are sqlite-vec virtual tables, created in Phase 20.

CREATE INDEX idx_messages_conversation ON messages(conversation_id);
CREATE INDEX idx_summaries_conversation ON summaries(conversation_id);
CREATE INDEX idx_queue_undelivered ON notification_queue(delivered_at) WHERE delivered_at IS NULL;
CREATE INDEX idx_workflow_runs_conv ON workflow_runs(conversation_id);
CREATE INDEX idx_agent_runs_workflow ON agent_runs(workflow_run_id);
CREATE INDEX idx_lineage_target_gen ON evolution_lineage(target, generation);
CREATE INDEX idx_pending_undecided ON pending_promotions(decided_at) WHERE decided_at IS NULL;
```

The Memory Agent is the only writer to durable memory. Other agents return their findings; Memory Agent commits.

## Tool Discipline

v0.1 exposes a small, curated tool surface to agents:

- **Constrained-bash skill** (Phase 11, sandboxed Phase 15): Execution Agent invokes shell scripts via this skill. Filesystem allowlist, env restriction, timeout.
- **Memory access**: indirect, through Memory Agent. No direct DB access from worker agents.
- **Vault read**: Research and Memory agents can read vault files.

When new capabilities arrive in v0.2+, prefer **CLI scripts invoked through the constrained-bash skill** over first-class tool definitions.

## Configuration Files

### `.env.example`

```dotenv
# Required (v0.1)
OPENROUTER_API_KEY=

# Optional, for future phases (v0.2+)
TELEGRAM_BOT_TOKEN=
GOOGLE_CALENDAR_CLIENT_ID=
GOOGLE_CALENDAR_CLIENT_SECRET=
GMAIL_CLIENT_ID=
GMAIL_CLIENT_SECRET=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
```

### `config/settings.yaml`

```yaml
models:
  classifier: openrouter/qwen/qwen-2.5-7b-instruct
  default: openrouter/anthropic/claude-sonnet-4.5
  casual: openrouter/anthropic/claude-haiku-4.5
  compaction: openrouter/anthropic/claude-haiku-4.5
  evaluator: openrouter/anthropic/claude-sonnet-4.5
  critic: openrouter/anthropic/claude-sonnet-4.5
  coding: openrouter/anthropic/claude-sonnet-4.5
  evolution_generator: openrouter/anthropic/claude-sonnet-4.5

api_keys:
  openrouter:
    env: OPENROUTER_API_KEY

memory:
  recall_turns: 10
  session_timeout_minutes: 30
  compaction:
    strategy: default
    trigger_at_turns: 30
  embeddings:
    enabled: true
    model: openrouter/openai/text-embedding-3-small
    recall_top_k: 5

vault:
  path: ./vault
  daily_notes_subdir: daily

governance:
  approval_required_on:
    - destructive
    - irreversible_high_risk
  confidence_threshold_for_auto: 0.7

evolution:
  enabled: true
  max_calls_per_hour: 30
  population_size: 8
  generations_per_run: 3
  cron: null
  fitness_weights:
    success_rate: 0.40
    cost_inverse: 0.15
    latency_inverse: 0.10
    hallucination_inverse: 0.20
    user_correction_inverse: 0.15

logging:
  level: INFO
  format: json
```

### `config/routing.yaml`

```yaml
rules:
  - match: { intent: technical }
    workflow: technical_deep
  - match: { intent: work, task_type: command }
    workflow: quick_action
  - match: { intent: casual }
    workflow: casual_reply
  - match: { tone: frustrated }
    workflow: supportive_reply
  - match: { intent: research }
    workflow: research_brief
  - match: { intent: coding }
    workflow: coding_session
  - match: { task_type: high_stakes_decision }
    workflow: debate_then_synthesize
default_workflow: casual_reply
```

### `config/workflows.yaml`

```yaml
workflows:
  technical_deep:
    persona: architect
    agents: [research, evaluator, persona]
    mode: sequential
    risk: low
  quick_action:
    persona: operator
    agents: [persona]
    mode: sequential
    risk: low
  casual_reply:
    persona: casual
    agents: [persona]
    mode: sequential
    risk: low
  supportive_reply:
    persona: casual
    agents: [persona]
    mode: sequential
    risk: low
  research_brief:
    persona: architect
    agents: [research, critic, evaluator, persona]
    mode: collaborative
    risk: low
  coding_session:
    persona: architect
    agents: [coding, evaluator, persona]
    mode: sequential
    risk: medium
  debate_then_synthesize:
    persona: architect
    agents: [research, critic, evaluator, persona]
    mode: debate
    risk: medium
  speculative_brief:
    persona: operator
    agents: [research_cheap, research_strong, evaluator, persona]
    mode: speculative
    risk: low
```

### `config/governance.yaml`

```yaml
risk_rules:
  - skill: constrained-bash
    risk: medium
  - intent: notification_control
    risk: low
  - tool: any_external_write
    risk: high
  - destructive_keywords: ["delete", "drop", "rm -rf", "force push"]
    risk: destructive

decision_thresholds:
  auto_max_risk: low
  approval_min_risk: high
  reject_below_confidence: 0.2
```

## CLAUDE.md (for future Claude Code sessions)

The project ships [CLAUDE.md](CLAUDE.md) at the root. It contains the project description, what's in scope and out, current phase status (pointing at [STATUS.md](STATUS.md)), conventions (prose over bullets, no em-dashes, no emojis, direct tone), and architectural rules (Master Agent orchestrates, every outbound message through the queue, secrets only in `.env`, new behavior as event handlers, new capabilities default to CLI scripts invoked via the constrained-bash skill, every implementation phase on its own branch with merge gated by user approval).

---

## Phased Build Plan

22 phases organized into 6 tiers. Each phase ends with a working, end-to-end-testable system. The cumulative manual playbook lives at `tests/manual/smoke_test.md` and grows as phases land.

**Branch workflow:** for each phase N, create branch `phase-N-<short-name>` off the latest `main`. All commits for that phase land on the branch. The user reviews when the testing plan and smoke test pass; merging to `main` is the user's call. Don't start phase N+1 until phase N is merged.

### Tier 1 — Foundation (Phases 0–7)

These phases reach a working single-agent CLI that classifies, routes, persists, projects to vault, runs skills, and queues all output. By the end of Tier 1, the substrate for the multi-agent runtime in Tier 2 is ready.

### Phase 0 — Skeleton

**Branch:** `phase-0-skeleton`

**Goal:** A `uv run python -m ubongo` invocation that loads config, sets up structured logging, and exits cleanly. Pure scaffolding.

**Sub-phases:**

- **0a — Project init.** `uv init`; `pyproject.toml` with deps (`litellm`, `python-dotenv`, `pyyaml`, `pytest`, `sqlite-vec`); `uv sync` works.
- **0b — Config loading.** `config.py` reads `settings.yaml`, resolves env-var refs, validates required fields.
- **0c — Hierarchical context loader.** `context.py` provides `build_system_prompt(persona, skill=None, agent_role=None)`.
- **0d — Structured JSON logging.** `logging.py`; configurable level; one startup log line with config summary (no secrets).
- **0e — CLI entry.** `__main__.py` with argparse; default action prints startup line; `send` subcommand parsed but no-op.

**Files touched:** `pyproject.toml`, `src/ubongo/__init__.py`, `src/ubongo/__main__.py`, `src/ubongo/config.py`, `src/ubongo/context.py`, `src/ubongo/logging.py`, `config/settings.yaml`, `config/UBONGO.md`, `config/personas/{architect,operator,casual}.md`, `.env.example`.

**Testing plan:**

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Cold start | `uv run python -m ubongo` | JSON startup line; rc 0. |
| 2 | Missing API key | unset `OPENROUTER_API_KEY`; run | rc 1; clear error pointing at the missing var. |
| 3 | Context assembly | `python -c "from ubongo.context import build_system_prompt; print(build_system_prompt('architect'))"` | UBONGO.md body, blank line, architect.md body. |
| 4 | Log structure | Capture stderr; pipe to `jq .` | Valid JSON. Has `event`, `level`, `ts`. No secrets. |

**End-to-end manual smoke test:** N/A for Phase 0 (no user-facing surface yet). The smoke playbook starts in Phase 1.

**Acceptance:** all 4 scenarios pass; merge `phase-0-skeleton` → `main` after user approval.

### Phase 1 — CLI REPL + One-Shot (echo mode)

**Branch:** `phase-1-cli-echo`

**Goal:** REPL accepts input and echoes back with current persona name. One-shot mode runs a single turn and exits. Slash commands switch personas. No LLM yet.

**Sub-phases:**

- **1a — REPL loop** (`repl.py`): prompt `> `; read line; dispatch.
- **1b — One-shot command** (`oneshot.py`): parse `send "<msg>" [--persona <name>]`; run one turn; exit.
- **1c — Slash command parser**: `/architect`, `/operator`, `/casual`, `/auto`, `/exit`. Active persona stored in-process.
- **1d — Echo response**: output `[<persona>] <input>` for any text turn.
- **1e — `__main__.py` dispatch**: no args → REPL; `send <msg>` → one-shot.

**Files touched:** `src/ubongo/repl.py`, `src/ubongo/oneshot.py`, `src/ubongo/__main__.py`.

**Testing plan:**

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | REPL echo | start REPL; type "hello" | `[architect] hello` |
| 2 | Persona switch | `/casual`; type "hello" | `[casual] hello` |
| 3 | `/auto` | after `/casual`, type `/auto`; "hello" | `[architect] hello` (default) |
| 4 | One-shot | `python -m ubongo send "hello" --persona operator` | `[operator] hello`; rc 0. |
| 5 | `/exit` | type `/exit` | clean exit, rc 0. |

**End-to-end smoke test (initialize `tests/manual/smoke_test.md`):**

1. `python -m ubongo`. Confirm startup log + REPL prompt.
2. Try `/architect`, `/operator`, `/casual` and a message after each.
3. `/exit`.
4. `python -m ubongo send "hi" --persona casual` returns `[casual] hi`.

**Acceptance:** all 5 scenarios pass; merge after approval.

### Phase 2 — LLM Integration

**Branch:** `phase-2-llm`

**Goal:** Real responses through LiteLLM/OpenRouter using hierarchical prompts. Personas feel different.

**Sub-phases:**

- **2a — Persona registry** (`agents/personas.py`): load each persona file (frontmatter + body) at startup. Frontmatter declares `default_model`, `max_tokens`.
- **2b — LiteLLM wrapper** (`llm.py`): `complete(system_prompt, messages, model, max_tokens) -> CompletionResult`. Single retry on transient errors.
- **2c — Wire into REPL/oneshot.** Replace echo: build system prompt, call LLM, return text.
- **2d — Event scaffolding.** Emit `before_llm` / `after_llm` (passthroughs).
- **2e — Error path.** On terminal LLM error: short polite message, log cause.

**Files touched:** `src/ubongo/agents/personas.py`, `src/ubongo/llm.py`, `src/ubongo/repl.py`, `src/ubongo/oneshot.py`, `src/ubongo/events.py`.

**Testing plan:**

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Architect mode | "design a circuit breaker for an API gateway" | substantive technical response with tradeoff discussion. |
| 2 | Casual mode | "ugh today sucked" | short, warm reply. |
| 3 | Operator mode | "summarize my last 3 commits" | terse, action-oriented (LLM may caveat about no git access — acceptable). |
| 4 | UBONGO.md effect | edit `config/UBONGO.md` to add a quirky preference; restart; ask any question | response respects the new preference. |
| 5 | LLM error | bogus API key | polite error message; no traceback to stdout. |

**Smoke additions:** Architect/Operator/Casual each respond and feel different; one-shot factual works.

**Acceptance:** scenarios pass; smoke passes; merge after approval.

### Phase 3 — Tone Classifier + Auto Routing

**Branch:** `phase-3-classifier`

**Goal:** In `/auto` mode, the system classifies each message and picks the persona automatically. Slash overrides still work.

**Sub-phases:**

- **3a — Classifier function** (`classifier.py`): single LLM call to small classifier model; JSON output `{intent, tone, task_type, suggested_skill, risk, confidence}`. Defensive parsing.
- **3b — Routing logic** (`router.py`): load `routing.yaml`; apply rules; return persona. Becomes a private helper of Master Agent in Phase 8.
- **3c — Hysteresis.** Only switch persona on confidence > 0.7 AND new persona suggestion.
- **3d — Wire `before_classify` / `after_classify` events** (passthrough).
- **3e — Per-turn classification log.**

**Files touched:** `src/ubongo/classifier.py`, `src/ubongo/router.py`, `config/routing.yaml`, `src/ubongo/repl.py`, `src/ubongo/oneshot.py`.

**Testing plan:**

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Auto-route to architect | `/auto`; "help me design a circuit breaker" | architect persona; architect-style response. |
| 2 | Auto-route to casual | `/auto`; "ugh long day" | casual persona. |
| 3 | Hysteresis | five technical messages, then "lol" | persona stays architect. |
| 4 | Manual override beats auto | `/auto`; technical question; `/casual` for next | casual voice for next response. |
| 5 | Classifier failure | force JSON parse error | falls back to default persona; logs failure. |

**Smoke additions:** auto picks reasonable personas across mixed conversation.

**Acceptance:** scenarios pass; merge after approval.

### Phase 4 — SQLite Memory + Compaction

**Branch:** `phase-4-memory`

**Goal:** Conversations persist across restarts. Recall returns recent turns + a compaction summary for older history.

**Sub-phases:**

- **4a — Schema + migrations** (`memory/schema.sql`); `CREATE IF NOT EXISTS` for all tables (including the multi-agent / governance / evolution tables, empty for now).
- **4b — Store API** (`memory/store.py`): start/get/end conversations; append messages; get session state; get last N; persist summaries.
- **4c — Session definition.** Same user, last_message_at gap < 30 minutes.
- **4d — Compaction** (`memory/compaction.py`): registry pattern; default impl summarizes older messages into one paragraph.
- **4e — Wire `after_recall` event** (compaction handler attached).
- **4f — Wire `after_llm` event** (memory write handler attached).
- **4g — Move active persona / override into `sessions` table.**

**Files touched:** `src/ubongo/memory/schema.sql`, `src/ubongo/memory/store.py`, `src/ubongo/memory/compaction.py`, `src/ubongo/repl.py`, `src/ubongo/oneshot.py`, `src/ubongo/events.py`.

**Testing plan:**

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Persistence | 5-turn conversation; `/exit`; restart within 30 min | bot remembers the topic. |
| 2 | New session | 31 min silence; send a message | new `conversations` row; old context not recalled. |
| 3 | Compaction trigger | 31 turns | summary persisted; recall = summary + last 10. |
| 4 | Compaction idempotency | continue past 31 turns by 5 | existing summary not re-generated. |
| 5 | Swappable strategy | register stub returning `"STUB"`; trigger | recall uses `"STUB"`. |

**Smoke additions:** restart-resumes-conversation; "what have we been talking about" coherent after ~30 turns.

**Acceptance:** scenarios pass; merge after approval.

### Phase 5 — Markdown Vault Projection

**Branch:** `phase-5-vault`

**Goal:** Daily notes generated in Obsidian-compatible Markdown. Read-only in v0.1 (sync is Phase 21).

**Sub-phases:**

- **5a — Vault writer** (`memory/vault.py`): `append_to_daily_note(date, user_message, response, persona)`.
- **5b — Default `after_send` handler** calls vault writer.
- **5c — Vault structure**: `vault/daily/YYYY-MM-DD.md`, lazy mkdir.
- **5d — Obsidian-compatibility check.**

**Files touched:** `src/ubongo/memory/vault.py`, `src/ubongo/events.py`, `vault/.gitkeep`.

**Testing plan:**

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Daily note write | send 3 messages | `vault/daily/<today>.md` has 3 entries. |
| 2 | Obsidian render | open `vault/` as Obsidian vault | renders cleanly. |
| 3 | Handler disable | unregister vault handler; send a message | no vault write; SQLite still updated. |
| 4 | Date rollover | mock time forward; send a message | new `<tomorrow>.md`. |

**Smoke additions:** today's vault note has the conversation entries.

**Acceptance:** scenarios pass; merge after approval.

### Phase 6 — Skills + Progressive Disclosure

**Branch:** `phase-6-skills`

**Goal:** Skills as folders with frontmatter + body. Descriptions load at startup; bodies on activation. v0.1 ships `summarize-conversation`.

**Sub-phases:**

- **6a — Skill discovery** (`skills.py`): scan `config/skills/`; parse `SKILL.md` frontmatter only; build registry.
- **6b — Lazy body loading.** Body read on first activation; cached. `/reload` clears cache.
- **6c — Classifier skill suggestion.** Pass list of skill names + descriptions. Expect `suggested_skill` in JSON.
- **6d — Skill resolution order.** Slash command > classifier suggestion > manual `/skill <name>`.
- **6e — `summarize-conversation` skill.** `/summary`; operator persona; 3-5 sentence summary.
- **6f — `/skills` and `/reload` REPL commands.**

**Files touched:** `src/ubongo/skills.py`, `config/skills/summarize-conversation/SKILL.md`, `config/skills/summarize-conversation/prompts/summarize.md`, `src/ubongo/repl.py`, `src/ubongo/classifier.py`.

**Testing plan:**

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | `/summary` works | 5-turn conversation; `/summary` | coherent 3-5 sentence operator-voice summary. |
| 2 | Skill catalog | `/skills` | lists `summarize-conversation`. |
| 3 | `/reload` | edit body; `/reload`; trigger again | new body in effect. |
| 4 | Body lazy-load | inspect logs at startup vs after `/summary` | not loaded at startup. |
| 5 | Classifier suggestion | "can you wrap this up for me" | suggested skill = `summarize-conversation`. |

**Smoke additions:** `/summary` and `/skills` both work.

**Acceptance:** scenarios pass; merge after approval.

### Phase 7 — Minimal Outbound Queue

**Branch:** `phase-7-queue`

**Goal:** Every CLI response flows through the SQLite-backed queue.

**Sub-phases:**

- **7a — Queue API** (`delivery/queue.py`): `enqueue`, `dequeue_deliverable`, `mark_delivered`.
- **7b — Refactor response path.** Enqueue at urgent; immediately dequeue + fire `before_send` (passthrough) + print + fire `after_send` (vault) + mark delivered.
- **7c — `/queue` REPL command.** Print last N rows.

**Files touched:** `src/ubongo/delivery/queue.py`, `src/ubongo/repl.py`, `src/ubongo/oneshot.py`, `src/ubongo/events.py`.

**Testing plan:**

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Queue contains response | send a message; query `notification_queue` | row with `delivered_at` set. |
| 2 | `/queue` | send 3 messages; `/queue` | 3-row table. |
| 3 | Latency | time round-trip vs Phase 6 | no perceptible delay. |
| 4 | Event hooks | register a no-op `before_send` handler; send | response delivered; handler runs. |
| 5 | Vault still works | check `vault/daily/<today>.md` | entry present. |

**Smoke additions:** `/queue` non-empty after a few turns; full Phase 1–7 walkthrough passes.

**Acceptance:** scenarios pass; **end of Tier 1**; merge after approval.

---

### Tier 2 — Multi-Agent System (Phases 8–12)

### Phase 8 — Master Agent

**Branch:** `phase-8-master`

**Goal:** The Master Agent wraps the existing single-agent flow without changing user-visible behavior. Establishes the seam workers will plug into in Phase 9.

**Sub-phases:**

- **8a — `MasterAgent` class** (`master.py`): `classify`, `plan`, `execute`, `decide`, `handle`. In Phase 8, `plan` always returns a one-agent workflow.
- **8b — Decision matrix scaffold** (`governance/decision.py`): returns `auto` for everything. Real rules ship in Phase 14.
- **8c — Migrate response path.** REPL/oneshot calls `MasterAgent.handle(message)`.
- **8d — Logging.** Emit `master_decision` log per turn with classification + workflow + decision.
- **8e — `/decisions` REPL command.**

**Files touched:** `src/ubongo/master.py`, `src/ubongo/governance/__init__.py`, `src/ubongo/governance/decision.py`, `src/ubongo/repl.py`, `src/ubongo/oneshot.py`, `src/ubongo/router.py`.

**Testing plan:**

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Behavior parity | same prompts as Phase 7 baseline | same responses (modulo nondeterminism). |
| 2 | Decision logged | "design a circuit breaker" | `master_decision` log: `intent=technical persona=architect mode=sequential risk=low`. |
| 3 | `/decisions` | send 3; `/decisions` | 3-row table. |
| 4 | High-risk passthrough | force `risk=high` | decision = `auto` (rules ship Phase 14). |
| 5 | Classifier crash | inject exception | falls back to default persona; logs error; response produced. |

**Smoke additions:** `/decisions` populated; cumulative Phase 1–8 walkthrough passes.

**Acceptance:** scenarios pass; merge after approval.

### Phase 9 — First Workers (Research + Memory)

**Branch:** `phase-9-research-memory`

**Goal:** Real worker agents enter the system. Master Agent picks Research for research-y intents; Memory Agent becomes the single writer to durable memory.

**Sub-phases:**

- **9a — Agent base** (`agents/base.py`): `Agent` protocol; `AgentInput`, `AgentResult` dataclasses; `agent_started` / `agent_completed` / `agent_failed` events.
- **9b — Research Agent** (`agents/research.py`): retrieval + synthesis over conversation memory + vault snippets.
- **9c — Memory Agent** (`agents/memory.py`): single writer for `messages`, `summaries`, `facts`, vault, embeddings.
- **9d — Workflow runner skeleton** (`runner.py`): sequential mode only; `execute(workflow) -> WorkflowResult`.
- **9e — Master Agent picks Research** for `research_brief` workflow.
- **9f — `/agents` REPL command.**
- **9g — `workflow_runs` and `agent_runs` writes.**

**Files touched:** `src/ubongo/agents/base.py`, `src/ubongo/agents/research.py`, `src/ubongo/agents/memory.py`, `src/ubongo/runner.py`, `src/ubongo/master.py`, `config/workflows.yaml`, `src/ubongo/repl.py`.

**Testing plan:**

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Research dispatched | "research what we discussed about caching" | Research run visible in `agent_runs`; response cites prior turns. |
| 2 | `/agents` | `/agents` | lists registered workers. |
| 3 | Workflow trace | latest `workflow_runs` row | execution_mode=sequential; corresponding agent_runs. |
| 4 | Memory single-writer | non-Memory agent attempts write (test mode) | raises; no DB write outside Memory Agent. |
| 5 | Casual still works | `/casual`; "long day" | single-agent (persona only); casual voice. |

**Smoke additions:** research-style question shows multi-agent run; cumulative walkthrough passes.

**Acceptance:** scenarios pass; merge after approval.

### Phase 10 — Evaluator + Critic + Persona Agents Formalized

**Branch:** `phase-10-evaluator-critic`

**Goal:** Three more workers; persona switching now goes through Persona Agent classes; Evaluator produces a confidence number used by governance.

**Sub-phases:**

- **10a — Evaluator Agent** (`agents/evaluator.py`): LLM-as-judge; confidence score + flagged issues.
- **10b — Critic Agent** (`agents/critic.py`): contrarian frame.
- **10c — Persona Agents** (`agents/personas.py`): class-based; `ArchitectPersona`, `OperatorPersona`, `CasualPersona`.
- **10d — Master Agent uses Evaluator** before governance.
- **10e — `/trace <n>` REPL command.**

**Files touched:** `src/ubongo/agents/evaluator.py`, `src/ubongo/agents/critic.py`, `src/ubongo/agents/personas.py`, `src/ubongo/master.py`, `src/ubongo/governance/decision.py`, `src/ubongo/repl.py`.

**Testing plan:**

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Evaluator runs | any technical question | `agent_runs` row for `evaluator`; confidence stored. |
| 2 | Critic invocation | borderline confidence (test) | Critic runs; response references critique. |
| 3 | Persona Agent classes | `/architect`; ask question | `agent_runs.agent='architect'`. |
| 4 | `/trace 1` | after a turn | classification, workflow, agent runs in order with timings. |
| 5 | Confidence in decision | force evaluator < 0.2 | decision = reject (Phase 14 thresholds, stub). |

**Smoke additions:** `/trace 1` shows multi-agent execution.

**Acceptance:** scenarios pass; merge after approval.

### Phase 11 — Coding + Execution + Repair Agents

**Branch:** `phase-11-remaining-workers`

**Goal:** Remaining workers ship. Execution Agent runs shell scripts via constrained-bash skill; Repair Agent registered with single-retry.

**Sub-phases:**

- **11a — Coding Agent** (`agents/coding.py`).
- **11b — Constrained-bash skill** (`config/skills/constrained-bash/`): risk: medium, reversibility: irreversible. v0.1 enforcement: subprocess + restricted PATH.
- **11c — Execution Agent** (`agents/execution.py`): invokes constrained-bash.
- **11d — Repair Agent registered** (`agents/repair.py`): single-retry on `agent_failed`.
- **11e — `/exec <cmd>` REPL command** (debug only).
- **11f — Workflow `coding_session` lit up.**

**Files touched:** `src/ubongo/agents/coding.py`, `src/ubongo/agents/execution.py`, `src/ubongo/agents/repair.py`, `config/skills/constrained-bash/SKILL.md`, `config/skills/constrained-bash/prompts/run.md`, `config/workflows.yaml`, `src/ubongo/master.py`.

**Testing plan:**

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Coding Agent | "write a Python function that reverses a list" | response contains a tested-shape function. |
| 2 | Execution path | "run `ls` in the project root" | constrained-bash runs; directory listing returned. |
| 3 | Sandbox guard | `cat /etc/passwd` | refused; clear error; no leak. |
| 4 | Repair single-retry | force Coding to fail once | retry with alternate model succeeds; trace shows two `agent_runs`. |
| 5 | `/exec` direct | `/exec "echo hello"` | prints `hello`. |

**Smoke additions:** code request returns code; "run `ls`" works; forced failure recovers transparently.

**Acceptance:** scenarios pass; merge after approval.

### Phase 12 — Execution Modes (all six)

**Branch:** `phase-12-modes`

**Goal:** Workflow Runner supports sequential, parallel, competitive, collaborative, debate, speculative.

**Sub-phases:**

- **12a — Parallel mode** (`asyncio.gather`).
- **12b — Competitive mode.** Same input; Evaluator picks winner.
- **12c — Collaborative mode.** Per-agent subtask; structural merge.
- **12d — Debate mode.** Two agents argue N rounds; Evaluator synthesizes.
- **12e — Speculative mode.** Cheap-first; strong validates in background; follow-up correction if mismatch.
- **12f — Mode selection** declared in `workflows.yaml`.
- **12g — `/mode <workflow>` debug command.**

**Files touched:** `src/ubongo/runner.py`, `src/ubongo/master.py`, `src/ubongo/agents/evaluator.py`, `config/workflows.yaml`, `src/ubongo/repl.py`.

**Testing plan:**

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Sequential | standard architect question | same as Phase 10. |
| 2 | Parallel | `/mode research_brief`; "compare Postgres vs DynamoDB" | concurrent runs; total latency < sequential. |
| 3 | Competitive | configure `coding_competitive` test workflow; ask coding question | both run; Evaluator picks winner with reasoning. |
| 4 | Collaborative | `/mode research_brief` with `mode=collaborative` | Research = facts; Critic = risks; merged brief. |
| 5 | Debate | `/mode debate_then_synthesize`; "should we use microservices for a 5-engineer team" | 2 rounds; Evaluator synthesizes "no, with caveats". |
| 6 | Speculative | `/mode speculative_brief`; quick factual question | cheap response immediate; if validation contradicts, follow-up correction within ~10s. |

**Smoke additions:** all six modes via `/mode` produce distinguishable behavior.

**Acceptance:** scenarios pass; **end of Tier 2**; merge after approval.

---

### Tier 3 — Self-Healing (Phase 13)

### Phase 13 — Repair Agent Activated

**Branch:** `phase-13-repair`

**Goal:** Real failure detection + multi-step recovery.

**Sub-phases:**

- **13a — Failure taxonomy** (`agents/repair.py`): timeout, model error, parse error, content rejection, infinite loop.
- **13b — Multi-strategy retry.** Same model + different prompt; different model same prompt; smaller model + shorter prompt; abort + apologize.
- **13c — Agent replacement.** Fallbacks declared per worker in `settings.yaml`.
- **13d — Workflow rollback.** Write-buffer pattern: agents queue writes; Memory Agent commits on success, drops on failure.
- **13e — Repair audit.** `repair_runs` table linking to `workflow_runs`.
- **13f — User-visible behavior.** On unrecoverable failure: clear apology + y/n.

**Files touched:** `src/ubongo/agents/repair.py`, `src/ubongo/runner.py`, `src/ubongo/agents/memory.py`, `src/ubongo/master.py`, `src/ubongo/memory/schema.sql` (add `repair_runs`).

**Testing plan:**

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Timeout recovery | inject 30s timeout in coding model | retry with smaller model; response within ~15s. |
| 2 | Parse error recovery | inject malformed JSON from classifier | re-prompt with stricter schema; success. |
| 3 | Agent replacement | disable Coding Agent | architect persona used for code questions; substitution logged. |
| 4 | Rollback | inject mid-collaborative failure | no partial messages persisted; vault unaffected. |
| 5 | Unrecoverable | inject persistent failure across all retries | apology + y/n; "n" returns to clean prompt. |

**Smoke additions:** manually trigger a failure; confirm graceful recovery.

**Acceptance:** scenarios pass; merge after approval.

---

### Tier 4 — Governance (Phases 14–15)

### Phase 14 — Risk + Confidence Scoring

**Branch:** `phase-14-governance-rules`

**Goal:** Decision matrix actually decides.

**Sub-phases:**

- **14a — `governance.yaml`** rules.
- **14b — `governance/risk.py`.**
- **14c — `governance/confidence.py`.**
- **14d — `governance/reversibility.py`.**
- **14e — `governance/decision.py`** rules combine into action.
- **14f — `/policy` REPL command.**
- **14g — `governance_decisions` writes.**

**Files touched:** `config/governance.yaml`, `src/ubongo/governance/{risk,confidence,reversibility,decision}.py`, `src/ubongo/master.py`, `src/ubongo/repl.py`.

**Testing plan:**

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Auto-approve | casual question | decision = auto. |
| 2 | Reject low confidence | force evaluator < 0.2 | decision = reject; user gets retry-different prompt. |
| 3 | Ask clarification | "delete what?" | decision = ask_clarification; persona asks for missing detail. |
| 4 | Require approval | "delete the entire vault" | decision = require_approval; blocks. |
| 5 | `/policy` | `/policy` | prints rules + thresholds. |

**Smoke additions:** normal turns auto-approve; destructive phrase triggers gate.

**Acceptance:** scenarios pass; merge after approval.

### Phase 15 — Approval Gates + Sandboxing

**Branch:** `phase-15-approval-sandbox`

**Goal:** Text-confirmation flow for `require_approval`. Execution Agent properly sandboxed.

**Sub-phases:**

- **15a — Approval prompt** (`governance/approval.py`): one-line summary + `confirm? (y/n/why)`.
- **15b — Approval persisted** in `governance_decisions.approval_response`.
- **15c — Execution Agent sandbox.** Subprocess: empty PATH, restricted env, CWD = project subdir, filesystem allowlist, no network, 10s timeout.
- **15d — Sandbox tests** (negative: out-of-allowlist, network, timeout).
- **15e — Documentation** in this file or `docs/SECURITY.md`.

**Files touched:** `src/ubongo/governance/approval.py`, `src/ubongo/agents/execution.py`, `src/ubongo/governance/decision.py`, `src/ubongo/repl.py`.

**Testing plan:**

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Approval yes | destructive ask; `y` | proceeds; logged. |
| 2 | Approval no | `n` | aborted; user back at prompt. |
| 3 | Approval why | `why` | one-paragraph risk explanation; re-prompts y/n. |
| 4 | Sandbox path violation | constrained-bash → `/etc/passwd` | refused; no read. |
| 5 | Sandbox timeout | `sleep 30` | killed at 10s. |
| 6 | Network blocked | `curl example.com` | fails. |

**Smoke additions:** approval prompt for destructive ask; sandbox refusal logged.

**Acceptance:** scenarios pass; **end of Tier 4**; merge after approval.

---

### Tier 5 — Self-Improvement (Phases 16–19)

### Phase 16 — Variant Generation

**Branch:** `phase-16-variants`

**Goal:** `/optimize <target>` generates N prompt variants. No autonomous loop yet.

**Sub-phases:**

- **16a — `evolution/generator.py`** strategies: paraphrase, prune, expand, recombine, perturb-temperature.
- **16b — Target registry** (`evolution/targets.py`).
- **16c — Variant persistence** to `evolution_lineage`.
- **16d — `/optimize <target>` REPL command.**

**Files touched:** `src/ubongo/evolution/__init__.py`, `src/ubongo/evolution/generator.py`, `src/ubongo/evolution/targets.py`, `src/ubongo/evolution/lineage.py`, `src/ubongo/repl.py`.

**Testing plan:**

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | `/optimize persona:architect` | run | 8 variants printed; 8 lineage rows for target=`persona:architect`, generation=1. |
| 2 | Strategy diversity | inspect variants | not all paraphrases. |
| 3 | Lineage parent | each row's `parent_id` points to current active. | |
| 4 | Targets list | `/optimize` no args | lists evolvable targets. |

**Smoke additions:** `/optimize persona:casual` produces 8 plausible alternates.

**Acceptance:** scenarios pass; merge after approval.

### Phase 17 — Sandboxed Evaluation + Fitness

**Branch:** `phase-17-evaluation`

**Goal:** Variants evaluated against held-out conversation sample; fitness computed.

**Sub-phases:**

- **17a — Held-out sample** (`tests/manual/fixtures/sample_conversations.json`): 30+ short conversations, anonymized.
- **17b — `evolution/sandbox.py`.**
- **17c — `evolution/fitness.py`** weighted sum.
- **17d — `evolution_evaluations` writes.**
- **17e — `/evaluate <target>` REPL command.**
- **17f — Anti-cost safeguards** (call cap, throttle).

**Files touched:** `src/ubongo/evolution/sandbox.py`, `src/ubongo/evolution/fitness.py`, `tests/manual/fixtures/sample_conversations.json`, `src/ubongo/repl.py`.

**Testing plan:**

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | `/evaluate persona:architect` | after Phase 16 generation | leaderboard with fitness. |
| 2 | Cost cap respected | `max_calls_per_hour=10`; trigger | throttles; partial results returned. |
| 3 | Hallucination signal | deliberately bad variant | fitness reflects degraded hallucination component. |
| 4 | Tiebreaker | near-equal variants | leaderboard shows both; pick deterministic on ties. |

**Smoke additions:** optimize-then-evaluate cycle for one persona produces leaderboard.

**Acceptance:** scenarios pass; merge after approval.

### Phase 18 — GP Loop (autonomous)

**Branch:** `phase-18-gp-loop`

**Goal:** Generations run as a background asyncio task. Throttled; pauseable.

**Sub-phases:**

- **18a — Loop driver** (`evolution/loop.py`): asyncio task; round-robin or staleness-based target selection.
- **18b — Throttle / scheduler.**
- **18c — Selection.** Top K survive; cross-generation lineage.
- **18d — `/evolution status` REPL command.**
- **18e — `/evolution pause`, `resume`, `off`.**
- **18f — Cron support.**

**Files touched:** `src/ubongo/evolution/loop.py`, `src/ubongo/evolution/selection.py`, `src/ubongo/repl.py`, `src/ubongo/__main__.py`.

**Testing plan:**

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Loop runs | `evolution.enabled=true`; wait 5 min | `evolution status` shows 1+ generation completed. |
| 2 | Pause | `/evolution pause` | no new generations until resume. |
| 3 | Throttle respect | `max_calls_per_hour=5` | ≤5 calls in test window. |
| 4 | Multi-target | three targets evolved | round-robin visible in lineage timestamps. |
| 5 | Crash recovery | kill REPL mid-generation; restart | resumes from last completed generation. |

**Smoke additions:** with evolution enabled, status populated after a few minutes.

**Acceptance:** scenarios pass; merge after approval.

### Phase 19 — GP Targets Expanded + Promotions

**Branch:** `phase-19-promotions`

**Goal:** Beyond persona prompts. Routing rules, tool chains, retry strategies all evolvable. Promotion flow lit up.

**Sub-phases:**

- **19a — Routing-rule variants.**
- **19b — Tool-chain variants** (per `workflows.yaml` workflow).
- **19c — Retry-strategy variants** for Repair Agent.
- **19d — Promotion queue.** `pending_promotions`.
- **19e — `/improvements` REPL command** (list with diffs).
- **19f — `/improvements approve <id>` / `reject <id>` / `rollback <target>`.**
- **19g — Audit log** at `vault/system/evolution-audit.md`.

**Files touched:** `src/ubongo/evolution/generator.py`, `src/ubongo/evolution/promotion.py`, `src/ubongo/repl.py`, `src/ubongo/master.py`, `vault/system/evolution-audit.md`.

**Testing plan:**

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Routing-rule variant | wait for evolution; `/improvements` | diff visible; fitness delta shown. |
| 2 | Approve | `/improvements approve <id>` | `active_evolutions` updated; audit row appended. |
| 3 | Reject | `/improvements reject <id>` | recorded; queue size decreases. |
| 4 | Live swap | after approval, ask normally-classified question | new rules in effect. |
| 5 | Rollback | `/improvements rollback <target>` | reverts cleanly. |

**Smoke additions:** `/improvements` non-empty after evolution; approve and confirm live behavior change.

**Acceptance:** scenarios pass; **end of Tier 5**; merge after approval.

---

### Tier 6 — Wiki Memory + Polish (Phases 20–21)

### Phase 20 — Embeddings + Graph

**Branch:** `phase-20-embeddings-graph`

**Goal:** Semantic recall and vault-link graph traversal.

**Sub-phases:**

- **20a — Embeddings tables** (`vec_messages`, `vec_vault` virtual tables).
- **20b — Embedding writes** by Memory Agent; idempotent on text change.
- **20c — Semantic recall handler** on `after_recall`; configurable top-K.
- **20d — Vault graph extraction.** Parse `[[wikilinks]]`; populate `vault_links`.
- **20e — Graph traversal API** (`memory/graph.py`).
- **20f — `/recall` REPL command.**

**Files touched:** `src/ubongo/memory/embeddings.py`, `src/ubongo/memory/graph.py`, `src/ubongo/agents/memory.py`, `src/ubongo/repl.py`.

**Testing plan:**

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Semantic recall | weeks-old caching discussion; today ask "remember our caching discussion" | old turns surface even if not in last-N. |
| 2 | Embedding idempotency | re-run on existing DB | no new calls for unchanged messages. |
| 3 | Vault graph | add `[[note-a]]` to a daily note | `vault_links` row appears; `neighbors` includes `note-a`. |
| 4 | `/recall` | after a turn | lists recency- and semantic-recalled items. |
| 5 | Without embeddings | `enabled=false`; restart | recency-only; no errors. |

**Smoke additions:** old-context recall demo (works after some history).

**Acceptance:** scenarios pass; merge after approval.

### Phase 21 — Bidirectional Vault Sync + Audit + End-to-End Tightening

**Branch:** `phase-21-vault-sync-audit`

**Goal:** User can edit vault files; system ingests changes. Full audit log of governance + evolution. Pre-flight smoke pass.

**Sub-phases:**

- **21a — File watcher** (`memory/vault.py` + watchdog).
- **21b — Ingest pipeline.** Edits trigger Memory Agent re-embed + diff; conflicts via Phase 15 approval flow.
- **21c — Audit log unified** at `vault/system/audit.md`.
- **21d — `/audit` REPL command** (filtered tail).
- **21e — Settings hot-reload** on `/reload`.
- **21f — Final smoke pass** end-to-end.

**Files touched:** `src/ubongo/memory/vault.py`, `src/ubongo/agents/memory.py`, `src/ubongo/governance/decision.py`, `src/ubongo/evolution/promotion.py`, `src/ubongo/repl.py`, `vault/system/audit.md`.

**Testing plan:**

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Vault edit ingestion | edit a daily note in Obsidian | within ~5s, ingest fires; embedding refreshed. |
| 2 | Conflict prompt | edit a vault file the system was about to write | approval prompt: keep mine / yours / merge. |
| 3 | Audit log | after governance + evolution events; `/audit` | tail; one row per event. |
| 4 | Settings hot-reload | edit `models.casual`; `/reload` | new model on next casual turn. |
| 5 | Full smoke | run full `smoke_test.md` | passes without manual fixup. |

**Smoke additions:** edit-vault-note loop; `/audit` walkthrough.

**Acceptance:** all scenarios pass; full smoke test passes; v0.1 done; merge after approval.

---

## Acceptance Criteria for v0.1 Complete

You are done when all of the following are true:

1. CLI REPL responds; one-shot command runs and exits.
2. Manual `/architect`, `/operator`, `/casual` work and feel different.
3. `/auto` mode classifies intent and routes appropriately; manual override beats auto.
4. `UBONGO.md` edits change behavior across personas after `/reload`.
5. Conversation context persists across CLI restarts within a session; new session after 30 minutes silence.
6. Compaction triggers past threshold; summaries persisted and not regenerated.
7. Daily notes write to vault and render in Obsidian.
8. `summarize-conversation` skill works via `/summary`; bodies lazy-loaded.
9. `/reload` picks up persona/skill metadata edits without restart.
10. Every outbound message goes through `notification_queue`.
11. Master Agent classifies, plans, dispatches, governs, composes per turn; `/decisions` and `/trace` populated.
12. All eight worker agents (Research, Coding, Evaluator, Repair, Memory, Critic, Execution, Persona) registered and dispatchable; `/agents` lists them.
13. All six execution modes (sequential, parallel, competitive, collaborative, debate, speculative) selectable via `/mode` and produce distinguishable behavior.
14. Repair Agent recovers timeouts, parse errors, agent failures; rollbacks leave no partial state.
15. Decision matrix returns auto / ask_clarification / require_approval / reject per the `governance.yaml` rules; `governance_decisions` populated.
16. `require_approval` flow prompts user; y/n/why all work; Execution Agent properly sandboxed.
17. `/optimize <target>` generates variants; `/evaluate` produces a fitness leaderboard.
18. GP loop runs autonomously when enabled; throttled; pauseable.
19. `/improvements` lists pending promotions with diffs; approve/reject works; live-target swap takes effect; rollback works.
20. Semantic recall via `sqlite-vec` augments recency in `/recall`; vault-link graph queryable.
21. File watcher ingests vault edits; conflicts gated by approval flow.
22. Full `tests/manual/smoke_test.md` walkthrough passes end-to-end without manual fixup.
23. Total project size stays under ~15,000 lines of Python (excluding tests). If significantly over, the spec is doing too much; cut.
24. Each phase landed via its own branch and was merged to `main` only after user approval.

If the system survives a real day of use without manual intervention or crashes, ship.

## Out of Scope (v0.1)

Deferred deliberately. Don't sneak them in.

- Telegram channel and any other external transport: Slack, WhatsApp, Discord, web UI, voice. Telegram returns in v0.2.
- Notification policy engine, quiet hours, ad-hoc holds, hold-until-ack, catch-up summarizer. Deferred to v0.2 with Telegram.
- External integrations: Google Calendar, Gmail, Reddit, news. v0.2+, one at a time, each as a CLI script invoked through the constrained-bash skill.
- Multi-user support, RBAC, team features.
- Distributed deployment: Docker, Kubernetes, Temporal, Redis, NATS.
- Production observability dashboards (structured logs + `/audit` are enough).
- Web UI / mobile apps.
- Approval gates beyond text confirmation (e.g., signed-action receipts).
- Bidirectional sync with non-vault sources (Notion, etc.).

## Setup Instructions (for the README)

```bash
# prerequisites
# - Python 3.11+
# - uv (https://docs.astral.sh/uv/)
# - An OpenRouter API key (https://openrouter.ai/)

git clone <repo>
cd ubongo
uv sync

# configure
cp .env.example .env
# edit .env with OPENROUTER_API_KEY
# edit config/UBONGO.md if you want to customize identity/preferences
# edit config/personas/*.md to tune voices

# run (REPL)
uv run python -m ubongo

# run (one-shot)
uv run python -m ubongo send "draft a migration plan"
```

## Final Notes for Claude Code

- Build phase by phase. Don't start Phase N+1 before Phase N's testing plan and smoke test pass. Acceptance criteria are literal.
- **Branch per phase.** Create `phase-N-<short-name>` off `main` at phase start. All commits for that phase land there. The user reviews when the testing plan and smoke test pass; merging to `main` is the user's call. Don't merge yourself.
- Keep modules small. If a file exceeds 400 lines, split. The orchestrator (`master.py`) is the exception.
- Write pytest tests for: classifier, master, runner, each agent, governance decision, evolution generator, evolution fitness, evolution lineage, memory store, memory compaction, memory embeddings, delivery queue, skills, events. Use the held-out sample fixture for evolution tests.
- No new dependencies beyond the tech-stack table without justification. In particular: no LangGraph, no Temporal, no `python-telegram-bot` until v0.2.
- The user's preferences apply to CLI output: prose over bullets, no em-dashes, no emojis, direct tone. These live in `config/UBONGO.md` and propagate via persona prompts.
- Every workflow goes through the queue. Every governance decision is persisted. Every agent run is traced. Every evolution variant has lineage. These are not optional.
- The Telegram channel in v0.2 should be additive: add `python-telegram-bot`, restore `allowed_user_ids` and the policy engine + quiet hours + holds, register the policy as a `before_send` handler. The router/agents/governance/evolution don't change.
