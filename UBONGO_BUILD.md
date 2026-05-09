# Ubongo - Build Specification

This document is the build spec for Ubongo. It is structured as phased releases with explicit acceptance criteria. v0.1 ships a useful, working system. v0.2 and v0.3 are sketched so that v0.1 makes structural choices that don't have to be undone later, but they are not part of the v0.1 build.

Build v0.1 first. Use it for two weeks. Then reassess what's actually missing before starting v0.2.

## What Ubongo Is

A personal, mood-aware AI assistant that lives in Telegram. One user (Giuseppe). Runs locally or on a small box (laptop, Pi 5). Adapts persona and model to the kind of conversation you're having. Capabilities are organized as composable skills with progressive disclosure. Outbound messages flow through a notification queue with a policy engine that respects quiet hours and ad-hoc holds. Extension points are named events so v0.2+ behavior plugs in without rewriting the core loop.

## What Ubongo Is Not (v0.1)

- A multi-agent orchestration platform
- A multi-channel system (no Slack, no WhatsApp, no Discord, no web UI)
- A self-improving runtime
- A production system
- A SaaS product

If a feature in the original PRD is not explicitly listed below, it is out of scope for v0.1. This includes: Master Agent, worker agents (Research/Coding/Evaluator/Repair/Execution), parallel agent execution, debate mode, speculative execution, Genetic Programming, runtime self-modification, prompt evolution, sandboxing, embedding-based memory recall, semantic search, observability dashboards, and approval gates beyond text confirmation.

## Core Design Decisions

1. **Single channel: Telegram.** Bot API is first-class, no daemon, no linked device. CLI may be added in v0.2.
2. **Router, not orchestrator.** A function that picks a persona, optionally one skill, and a model. No agent lifecycle, no spawning, no shutdown.
3. **Personas are configuration.** Markdown files containing system prompts. Personas define *voice*. Identity boilerplate is in a single global file (`UBONGO.md`), not duplicated across personas.
4. **Skills are configuration with progressive disclosure.** Folders with a `SKILL.md` and supporting prompts. Skills define *capabilities*. Only descriptions are loaded at startup; bodies load on activation. Personas and skills compose: any persona can run any skill.
5. **Hierarchical context loading.** System prompt for any turn is built by concatenating, in order: global context (`UBONGO.md`), active persona (`personas/<name>.md`), active skill body (if any). Closer-to-task layers come last.
6. **Memory: SQLite canonical, Markdown projected.** Conversation logs and structured facts live in SQLite. A daily notes Markdown file is generated for human inspection in Obsidian. Vault is write-only in v0.1.
7. **Compaction is a swappable function.** v0.1 ships a default (last N turns verbatim, prior history collapsed into a summary). The seam exists so v0.2 can swap in topic-aware or persona-specific strategies without touching memory storage.
8. **Models via OpenRouter.** Single API key, every model behind one interface, routed through LiteLLM. No Ollama in v0.1.
9. **All outbound messages flow through a notification queue.** Even synchronous responses to user messages technically pass through the queue, though they're delivered immediately at `urgent` priority. Proactive messages (when the scheduler exists) use the same path with no special-casing.
10. **Policy engine governs delivery.** Quiet hours, ad-hoc holds, hold-until-ack, and per-message urgency are first-class concepts.
11. **Extension points are named events.** The bot's main loop emits events at well-defined points (`before_classify`, `after_classify`, `before_recall`, `after_recall`, `before_llm`, `after_llm`, `before_send`, `after_send`). v0.1 has only default handlers; v0.2+ adds handlers without modifying the core loop.
12. **Tool discipline.** v0.1 exposes zero tools to the LLM. When tools are added (v0.2+), prefer CLI scripts invoked via a single `bash` tool over first-class tool definitions. New first-class tools require justification.
13. **Configuration in YAML, secrets in `.env`.** Code reads config; config never contains secrets; secrets are loaded from environment at startup.
14. **One user.** Hardcode the allowed Telegram user ID in config. Reject everything else.

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Language | Python 3.11+ | Ecosystem, LiteLLM, fast iteration |
| Bot | python-telegram-bot (v21+) | Mature, async, well-documented |
| LLM routing | LiteLLM | Provider abstraction without lock-in |
| Model provider | OpenRouter | Single key, every model, easy A/B |
| Storage | SQLite (stdlib) | Single-user, zero-ops, sufficient |
| Config | YAML + Markdown | Editable without touching code |
| Secrets | python-dotenv | Standard `.env` loading |
| Tests | pytest | Standard |
| Package management | uv | Fast, modern |

No FastAPI, no Redis, no Qdrant, no LangGraph, no Docker for v0.1. If you find yourself reaching for them, stop and justify.

## Architecture

```
Telegram message
    |
    v
auth check (allowlist)
    |
    v   [event: before_classify]
classifier (small model via OpenRouter, JSON output)
    |   -> {intent, tone, task_type, skill?, confidence}
    v   [event: after_classify]
router (deterministic mapping)
    |   -> (persona, skill?, model)
    v   [event: before_recall]
memory recall (SQLite, last N turns + compacted older history)
    |   [event: after_recall]
    v
prompt assembly (UBONGO.md + persona.md + skill.md? + history)
    |   [event: before_llm]
    v
LLM call (LiteLLM -> OpenRouter -> chosen model)
    |   [event: after_llm]
    v
enqueue response in notification queue (urgency=urgent)
    |
    v   [event: before_send] (policy engine runs as a handler here)
delivery worker checks policy, sends to Telegram
    |   [event: after_send]
    v
memory write (SQLite + Markdown daily note)
```

For proactive messages (v0.3+), the flow starts at "enqueue" and skips the user-message path. Same delivery worker, same policy engine, same `before_send` handlers.

## File Structure

```
ubongo/
  pyproject.toml
  README.md
  CLAUDE.md                    # context for future Claude Code sessions
  .env.example
  .gitignore                   # includes /vault, *.db, .env

  config/
    UBONGO.md                  # global identity and instructions (hierarchical root)
    settings.yaml              # general config
    routing.yaml               # tone/intent -> persona/skill rules
    urgency.yaml               # urgency assignment rules (used in v0.3)
    personas/
      architect.md             # voice-specific overlay only
      operator.md
      casual.md
    skills/
      summarize-conversation/
        SKILL.md
        prompts/
          summarize.md

  src/ubongo/
    __init__.py
    __main__.py                # entry point: python -m ubongo
    bot.py                     # Telegram handlers, main loop, event dispatch
    events.py                  # named event registry and dispatcher
    classifier.py              # tone/intent/skill classification
    router.py                  # persona + skill + model selection
    llm.py                     # LiteLLM wrapper
    context.py                 # hierarchical context loader (UBONGO.md + persona + skill)
    personas.py                # persona loading
    skills.py                  # skill registry: descriptions at startup, bodies on demand
    config.py                  # config loading, env var resolution
    delivery/
      __init__.py
      queue.py                 # notification queue (enqueue, dequeue)
      policy.py                # delivery policy engine (a before_send handler)
      worker.py                # background delivery loop
      catchup.py               # catch-up summarizer
      commands.py              # /hold, /resume, /quiet, /queue
    memory/
      __init__.py
      schema.sql
      store.py                 # SQLite operations
      compaction.py            # swappable compaction function (default impl in v0.1)
      vault.py                 # Markdown daily notes projection

  tests/
    test_classifier.py
    test_router.py
    test_skills.py
    test_context.py
    test_events.py
    test_memory.py
    test_compaction.py
    test_delivery_queue.py
    test_delivery_policy.py

  vault/                       # gitignored, generated daily notes
```

## Personas (v0.1)

Three personas. More can come later. Each lives in `config/personas/<name>.md` and contains *only* voice-specific instructions. Identity boilerplate (who Giuseppe is, communication preferences, formatting rules) lives in `config/UBONGO.md` and is loaded for every persona.

**Architect.** Deep technical reasoning. Used for system design, RFCs, code architecture, technical tradeoffs. Calm, structured, willing to push back. Heavy model.

**Operator.** Fast execution. Used for "do X" requests, status checks, quick lookups. Terse, direct, action-oriented. Heavy model but with low max_tokens.

**Casual.** Friendly conversation. Used for low-stakes chat, brainstorming, venting, end-of-day decompression. Warm, less structured. Lighter model is fine.

Example `config/UBONGO.md` (the global file, loaded for every turn):

```markdown
# Ubongo Global Context

You are Ubongo, Giuseppe Turitto's personal AI assistant.

## About Giuseppe
- Senior Engineering Manager at Kiwi.com, based in Madrid.
- Originally Venezuelan, lived in NYC, now in Madrid.
- 20+ years in software, 10+ in engineering leadership.
- Working on a leadership book ("Still Human") and several side projects.

## Communication Defaults
- Direct. Skip hedging and filler.
- Assume strong technical and management foundations.
- Push back when you see gaps in reasoning.
- Default to prose over bullet points unless he asks for a list.
- No em-dashes.
- No emojis unless he uses them first.
- Minimal markdown in conversation.

## Memory
You have access to memory of past conversations and a structured fact store.
Use it. Do not narrate retrieval.
```

Example `config/personas/architect.md` (voice overlay only, no identity boilerplate):

```markdown
---
name: architect
default_model: openrouter/anthropic/claude-sonnet-4.5
max_tokens: 4096
---

You are in Architect mode. Help Giuseppe with deep technical reasoning:
system design, architecture decisions, code structure, tradeoff analysis.

In this mode:
- Take time to think before answering complex design questions.
- Make tradeoffs explicit; never present a recommendation without naming what
  it costs.
- When the user proposes an architecture, identify at least one assumption
  worth challenging before agreeing.
- Use prose paragraphs. Reserve lists for genuinely list-shaped content.
```

The system prompt for an Architect-mode turn is `UBONGO.md` body + `architect.md` body, concatenated. If a skill is active, its body is appended after.

## Skills with Progressive Disclosure

Skills are reusable capabilities, separate from persona voice. Any persona can invoke any skill. A skill is a folder under `config/skills/` with this shape:

```
config/skills/<skill-name>/
  SKILL.md             # frontmatter (always loaded) + body (loaded on activation)
  prompts/             # optional, named prompt templates
    <name>.md
  references/          # optional, static reference material
    <name>.md
```

`SKILL.md` frontmatter:

```yaml
---
name: summarize-conversation
description: Produce a brief summary of the current conversation session. Activates on /summary or when the user asks to summarize what's been discussed.
trigger:
  commands: ["/summary"]
  intents: []
default_urgency: normal       # used when this skill produces proactive output (v0.3+)
default_persona: operator     # optional preferred persona; router can override
---
```

**Progressive disclosure.** At startup, the skill registry indexes each skill by name, description, and trigger metadata only. The body of `SKILL.md` is *not* loaded into memory. When the router selects a skill (by command match, intent match in the classifier output, or `/skill <name>`), the body is read from disk and appended to the system prompt for that turn only.

This matters because:

- The classifier sees the full list of skill *descriptions* and can include a `skill` field in its JSON output, naming a relevant skill from a possibly large library.
- The system prompt for each turn only includes the body of skills that are actually being used, not the entire skill catalog.
- Token cost scales with active skills, not registered skills. You can have 50 skills loaded without paying tokens for 49 of them on every turn.

**Skill resolution order:**

1. Explicit command (`/summary` -> `summarize-conversation`).
2. `skill` field in classifier JSON output (if non-null and matches a registered skill).
3. Manual selection via `/skill <name>` for the next message.

A message can have zero or one skill active in v0.1. Multi-skill composition is v0.2+.

**v0.1 ships exactly one skill: `summarize-conversation`.** The point in v0.1 is to validate the skills infrastructure and progressive disclosure, not to ship a library.

## Hierarchical Context Loader

Implemented in `context.py`. Single function `build_system_prompt(persona_name, skill_name=None) -> str` that:

1. Reads `config/UBONGO.md` body.
2. Reads `config/personas/<persona_name>.md` body (skipping frontmatter).
3. If `skill_name` is provided, reads `config/skills/<skill_name>/SKILL.md` body (skipping frontmatter), prefixed with `## Active Skill: <name>`.
4. Concatenates with double newlines between sections.
5. Returns the assembled string.

Caching: persona files and `UBONGO.md` are cached in memory at startup and on `/reload`. Skill bodies are read on demand and cached per skill name (cleared on `/reload`).

This means changing `UBONGO.md` updates every persona's behavior on next `/reload` without editing three files. Adding a new persona means writing only the voice-specific overlay, not duplicating identity.

## Named Events

Implemented in `events.py`. Simple synchronous dispatcher:

```python
class EventBus:
    def on(self, event: str, handler: Callable) -> None: ...
    def emit(self, event: str, payload: dict) -> dict: ...   # returns possibly-modified payload
```

Handlers receive a payload dict and return a (possibly modified) payload. Multiple handlers per event run in registration order. Default handlers ship in v0.1; v0.2+ adds handlers without modifying the bot loop.

**v0.1 events and their default handlers:**

| Event | Payload | Default handler |
|---|---|---|
| `before_classify` | `{message, session}` | none (passthrough) |
| `after_classify` | `{message, session, classification}` | none (passthrough) |
| `before_recall` | `{session, max_turns}` | none (passthrough) |
| `after_recall` | `{session, history}` | compaction (if history > N) |
| `before_llm` | `{system_prompt, messages, model}` | none (passthrough) |
| `after_llm` | `{response, tokens_in, tokens_out, model}` | memory write |
| `before_send` | `{queue_item, now}` | policy engine (decides deliver/hold) |
| `after_send` | `{queue_item, telegram_message_id}` | vault projection |

The bot loop calls `events.emit(...)` at each point and uses the returned payload. This is the seam for everything in v0.2+:

- Embedding-based recall: `after_recall` handler that augments `history` with semantically relevant past messages.
- Fact extraction: `after_llm` handler that runs over the user message and proposes facts to store.
- Redaction: `before_send` handler that scrubs sensitive content.
- Audit logging: handlers on every event writing to a structured log.

For v0.1, the only non-passthrough handlers are compaction (`after_recall`), memory write (`after_llm`), policy engine (`before_send`), and vault projection (`after_send`). All others are stubs that return the payload unchanged. The point of registering them in v0.1 is so the architecture is in place.

## Tone Classifier with Skill Selection

This is the part that has to actually work. v0.1 design:

**Single LLM call** to a small model via OpenRouter (e.g., `openrouter/qwen/qwen-2.5-7b-instruct` or `openrouter/meta-llama/llama-3.2-3b-instruct`). Prompt asks for structured JSON:

```json
{
  "intent": "technical|work|casual|research|notification_control|other",
  "tone": "neutral|focused|frustrated|playful",
  "task_type": "question|discussion|command|venting",
  "skill": null,
  "confidence": 0.0
}
```

The classifier prompt includes the list of available skill names and descriptions so the model can suggest one in the `skill` field, or `null` if none applies. This is how progressive disclosure connects to routing without an extra LLM call.

`notification_control` as an intent: messages like "hold notifications for 2 hours" or "stop bothering me until 6pm" are routed to the delivery commands handler in Phase 7, not to a normal LLM response.

**Routing rules** (`config/routing.yaml`):

```yaml
rules:
  - match: { intent: notification_control }
    handler: delivery_command       # bypass normal LLM flow
  - match: { intent: technical }
    persona: architect
  - match: { intent: work, task_type: command }
    persona: operator
  - match: { intent: casual }
    persona: casual
  - match: { tone: frustrated }
    persona: casual
default:
  persona: architect
```

**Hysteresis.** Within a single session (messages within 30 minutes of each other), the persona only switches if the new classification has confidence > 0.7 AND differs from the active persona. This prevents whiplash from a single offhand message.

**Manual override.** Slash commands `/architect`, `/operator`, `/casual` force the persona for the current session. `/auto` returns to automatic routing.

**Failure mode.** If classification fails or returns malformed JSON, fall back to the default persona with no skill. Log the failure. Never block on classifier failure.

## Memory Model

**SQLite as canonical store.** Schema:

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
  role TEXT NOT NULL,           -- user | assistant | system
  content TEXT NOT NULL,
  timestamp TIMESTAMP NOT NULL,
  persona TEXT,
  skill TEXT,
  model TEXT,
  tokens_in INTEGER,
  tokens_out INTEGER
);

CREATE TABLE summaries (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id),
  covers_from_message_id INTEGER NOT NULL,
  covers_to_message_id INTEGER NOT NULL,
  content TEXT NOT NULL,
  strategy TEXT NOT NULL,       -- name of the compaction function used
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
  subject TEXT NOT NULL,
  predicate TEXT NOT NULL,
  object TEXT NOT NULL,
  source_message_id INTEGER REFERENCES messages(id),
  created_at TIMESTAMP NOT NULL,
  importance INTEGER DEFAULT 0
);
-- facts table is created in v0.1 but population is v0.2+

CREATE TABLE notification_queue (
  id INTEGER PRIMARY KEY,
  content TEXT NOT NULL,
  urgency TEXT NOT NULL CHECK (urgency IN ('low', 'normal', 'urgent')),
  source TEXT,                  -- 'response' | 'skill:<name>' | 'job:<name>'
  source_skill TEXT,
  created_at TIMESTAMP NOT NULL,
  deliver_after TIMESTAMP,
  delivered_at TIMESTAMP,
  expires_at TIMESTAMP,
  metadata TEXT                 -- JSON blob
);

CREATE TABLE delivery_policy (
  id INTEGER PRIMARY KEY,
  type TEXT NOT NULL CHECK (type IN ('hold', 'hold_until_ack', 'quiet_hours_override')),
  until TIMESTAMP,
  urgency_threshold TEXT NOT NULL CHECK (urgency_threshold IN ('low', 'normal', 'urgent')),
  created_at TIMESTAMP NOT NULL,
  source TEXT,
  active BOOLEAN DEFAULT TRUE
);

CREATE INDEX idx_messages_conversation ON messages(conversation_id);
CREATE INDEX idx_summaries_conversation ON summaries(conversation_id);
CREATE INDEX idx_queue_undelivered ON notification_queue(delivered_at) WHERE delivered_at IS NULL;
CREATE INDEX idx_policy_active ON delivery_policy(active) WHERE active = TRUE;
```

**Recall and compaction.** `memory/compaction.py` defines a swappable function:

```python
def compact(history: list[Message], target_turns: int) -> tuple[Optional[Summary], list[Message]]:
    """
    Given a full message history and a target number of recent turns to keep
    verbatim, return (summary_of_older, recent_turns).
    Default v0.1 implementation: simple recency split with single-paragraph summary
    of everything older than `target_turns`.
    """
```

The default v0.1 implementation runs a small LLM call to summarize older messages into one paragraph. The summary is persisted in the `summaries` table so the same range isn't re-summarized on every turn. Recall returns: existing summary (if any) + the most recent N=10 messages.

This is the seam for v0.2+ to swap in topic-aware compaction (split history by topic shift, summarize each topic separately) or persona-specific compaction (architect persona preserves code blocks verbatim, casual persona summarizes aggressively) without touching `store.py`.

**Markdown projection.** A daily note at `vault/daily/YYYY-MM-DD.md` containing chronological conversation log with timestamps, persona, and content. Written by a default `after_send` handler.

```markdown
# 2026-05-07

## 09:14 [architect]
**You:** [message]
**Ubongo:** [response]

## 11:32 [casual]
...
```

The system reads from SQLite, never from Markdown. Bidirectional sync is v0.2+.

## Notification Queue and Delivery Policy

Every outbound Telegram message goes through the queue, even direct responses to user messages. This is structural, not optional. When v0.3 adds the scheduler, proactive messages use the same delivery path. If you start by sending some messages directly and others via the queue, you'll have to migrate every send site later. Build it once.

The policy engine is a `before_send` event handler. v0.2+ can add additional `before_send` handlers (redaction, format adjustment, urgency boosting based on rules) without modifying the worker.

**Synchronous responses** are enqueued with `urgency=urgent` and `deliver_after=NOW()`. The delivery worker picks them up immediately. To the user, this feels instant.

**Proactive messages** (v0.3+) are enqueued with their declared urgency and an optional `deliver_after`. The worker decides when they go.

**The policy engine.** Given a queued message and the current state, answers: "can this be delivered now?" using:

1. The message's urgency.
2. Active overrides in the `delivery_policy` table.
3. Quiet hours from `config/settings.yaml`.
4. Current time in the configured timezone.

A message is delivered iff its urgency meets or exceeds the highest currently active threshold.

**Slash commands:**

- `/hold 3h` — hold all non-urgent for 3 hours.
- `/hold until 18:00` — hold until 6pm today.
- `/hold` — `hold_until_ack`, no expiry.
- `/resume` — clear all active holds. Trigger catch-up summarization.
- `/queue` — list queued items by urgency.
- `/quiet` — show current quiet hours.
- `/quiet 22-08` — change quiet hours (runtime override).
- `/quiet off` — disable quiet hours.

**Natural language detection.** When the classifier returns `intent: notification_control`, the message is routed to a small handler that interprets the instruction and applies it via the same code paths as the slash commands. The handler confirms in plain language. Ambiguous phrasing falls back to suggesting slash commands.

**Catch-up on release.** When `/resume` runs (or a hold expires) and >= `summarize_threshold` items are pending, summarize them via an LLM call using the casual persona. Mark the underlying items delivered.

**Hold-until-ack safety.** If a `hold_until_ack` has been active for more than `hold_until_ack_warning_hours` (default 24), the worker sends a single urgent ping asking whether to keep holding.

**Worker loop.** Asyncio task in the same process as the bot. Wakes every `worker_poll_seconds` (default 30). Also wakes immediately when an item is enqueued at `urgent`.

**Expiry.** Items with `expires_at` in the past are dropped without delivery and logged.

## Tool Discipline

v0.1 exposes zero tools to the LLM. The LLM produces text; the bot sends text. No tool calling, no function calling.

When tools are added in v0.2+:

1. **Default to CLI scripts.** A new capability is a CLI binary or Python script with a README. The agent (when given tool access) uses a single `bash` tool to invoke it. The README is read on demand.
2. **First-class tools require justification.** A new entry in the LLM's tool list must justify why it can't be a CLI script. Acceptable reasons include latency-critical paths, structured output the LLM needs to reason about across turns, or required side-effect isolation.
3. **Tool descriptions are tax.** Every tool definition costs tokens on every turn it's available. Audit the tool list quarterly; remove unused tools.

This discipline is borrowed from Pi (the agent toolkit). The argument: progressive disclosure for capabilities, same as for skills. You'll have ten tools eventually if you're not careful, and most of them won't earn the context cost.

## Configuration Files

### `.env.example`

```
# Required
OPENROUTER_API_KEY=
TELEGRAM_BOT_TOKEN=

# Optional, for future phases
GOOGLE_CALENDAR_CLIENT_ID=
GOOGLE_CALENDAR_CLIENT_SECRET=
GMAIL_CLIENT_ID=
GMAIL_CLIENT_SECRET=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
```

### `config/settings.yaml`

```yaml
telegram:
  allowed_user_ids: [123456789]   # set to your Telegram numeric ID

models:
  default: openrouter/anthropic/claude-sonnet-4.5
  classifier: openrouter/qwen/qwen-2.5-7b-instruct
  casual: openrouter/anthropic/claude-haiku-4.5
  compaction: openrouter/anthropic/claude-haiku-4.5

api_keys:
  openrouter:
    env: OPENROUTER_API_KEY

memory:
  recall_turns: 10
  session_timeout_minutes: 30
  compaction:
    strategy: default              # name of registered compaction function
    trigger_at_turns: 30           # compact when total turns exceed this

vault:
  path: ./vault
  daily_notes_subdir: daily

delivery:
  quiet_hours:
    enabled: true
    start: "23:00"
    end: "07:00"
    timezone: "Europe/Madrid"
    urgency_threshold: urgent
  default_urgency_threshold: normal
  hold_until_ack_warning_hours: 24
  worker_poll_seconds: 30
  catchup:
    summarize: true
    summarize_persona: casual
    summarize_threshold: 2

logging:
  level: INFO
  format: json
```

### `config/routing.yaml`

See "Tone Classifier" section above.

### `config/urgency.yaml` (used in v0.3+, included in v0.1 as empty stub)

```yaml
rules: []
```

## CLAUDE.md (for future Claude Code sessions)

The project ships with a `CLAUDE.md` at the root containing:

- One-paragraph project description.
- "What Ubongo Is Not" (verbatim from this doc).
- Current phase status.
- Pointer to this build spec.
- Convention notes: prose over bullets, no em-dashes, no emojis, direct tone (Giuseppe's preferences, baked into the project).
- Architectural rules: every outbound message goes through the queue; secrets only in `.env`; new capabilities ship as skills, not as `bot.py` modifications; new behavior ships as event handlers, not as core-loop edits; new tools default to CLI scripts.

## Phased Build Plan

Each phase ships a working system. Don't move to phase N+1 until N is verified.

### Phase 0: Project Skeleton and Hierarchical Context

**Goal:** Working Python project with config loading, hierarchical context assembly, and structured logging.

**Tasks:**
- Initialize project with `uv init` and `pyproject.toml`.
- Create the file structure above.
- Write `.env.example` with required vars.
- Write `config/settings.yaml`, `config/UBONGO.md`, and stubs for `routing.yaml` and `urgency.yaml`.
- Write README.md with setup instructions.
- Write CLAUDE.md with project context.
- Implement `config.py`: load YAML, resolve env vars referenced in config, validate required fields at startup.
- Implement `context.py`: hierarchical loader that concatenates `UBONGO.md` + persona file + optional skill body.
- Implement `events.py`: EventBus with `on(event, handler)` and `emit(event, payload) -> payload`.
- Set up structured JSON logging to stderr.

**Acceptance:** `uv run python -m ubongo` starts, loads config, prints "Ubongo starting" with config summary (no secrets in the log), exits cleanly on Ctrl-C. Calling `context.build_system_prompt("architect")` returns the concatenation of `UBONGO.md` + `architect.md`. Registering a handler on a stub event and emitting it returns the modified payload.

### Phase 1: Telegram Echo Bot with Persona Switching

**Goal:** Bot responds to messages from the allowed user. Slash commands switch personas. Stub responses, no LLM yet.

**Tasks:**
- Implement `bot.py` with python-telegram-bot.
- Reject any message from a non-allowed user (silent drop, log it).
- Handle text messages: echo back with current persona name.
- Handle `/architect`, `/operator`, `/casual`, `/auto`, `/start`, `/help`.
- Active persona stored in-process for now; SQLite in Phase 4.

**Acceptance:** Send "hello" from your allowed account, get `[architect] hello`. Send `/casual`, send "hello", get `[casual] hello`. Send from another account, get nothing.

### Phase 2: LLM Integration via OpenRouter with Hierarchical Prompts

**Goal:** Real LLM responses through LiteLLM and OpenRouter. System prompts assembled via the hierarchical loader.

**Tasks:**
- Implement `personas.py`: load persona files (frontmatter + body) at startup.
- Implement `llm.py`: thin wrapper around LiteLLM. Function `complete(system_prompt, messages, model, max_tokens) -> CompletionResult`.
- Wire bot to: build system prompt via `context.build_system_prompt(persona)`, call LLM, return response.
- Wire `before_llm` and `after_llm` events (passthrough handlers in v0.1).
- Handle LLM errors: retry once with exponential backoff, then send a polite error message.

**Acceptance:** Send a technical question with `/architect`, get a real Architect-style response. Send a casual message with `/casual`, get a real Casual-style response. The personas feel different. Editing `UBONGO.md` and restarting changes behavior across all personas.

### Phase 3: Tone Classifier and Auto Routing

**Goal:** In `/auto` mode, the system classifies each message and picks the persona. Manual overrides still work.

**Tasks:**
- Implement `classifier.py`: function takes message, returns structured result. Use LiteLLM with the classifier model. JSON output mode if supported; defensive parsing otherwise. Classifier prompt includes empty skill list for now (skills come in Phase 6).
- Implement `router.py`: load `config/routing.yaml`, apply rules, return persona. Apply hysteresis logic.
- Wire into bot via `before_classify` and `after_classify` events (passthrough handlers).
- Log classifier output and routing decision for every message.

**Acceptance:** In `/auto` mode, "help me design a circuit breaker" routes to architect. "ugh today sucked" routes to casual. Five technical messages then one casual within a minute does not flip persona unless confidence is high.

### Phase 4: SQLite Conversation Memory with Swappable Compaction

**Goal:** Conversations persist across restarts. Recall returns recent turns plus a compaction summary for older history.

**Tasks:**
- Implement `memory/schema.sql`. Run migrations on startup (simple `CREATE IF NOT EXISTS`).
- Implement `memory/store.py`: start/get/end conversations, append messages, get session state, get last N messages, persist summaries.
- Define "session": same user, time gap < 30 minutes since last message.
- Implement `memory/compaction.py`: register a default compaction function. Trigger when total session turns exceed `compaction.trigger_at_turns`. Persist resulting summary in the `summaries` table.
- Wire `after_recall` event with the compaction handler.
- Wire `after_llm` event with the memory write handler.
- Move active persona / override state from in-process dict to `sessions` table.

**Acceptance:** Multi-turn conversation. Restart the bot. Continue; bot remembers last 10 turns plus a summary of older turns. Wait 31 minutes; next message starts a new session. Replace the default compaction function with a stub returning a fixed string; verify recall uses the stub.

### Phase 5: Markdown Vault Projection

**Goal:** Daily notes generated in Obsidian-compatible format, written by an `after_send` handler.

**Tasks:**
- Implement `memory/vault.py`: function `append_to_daily_note(date, user_message, bot_response, persona)`.
- Register as default `after_send` handler.
- Verify rendering in Obsidian.
- README notes that user can `git init` inside `vault/` for projection history.

**Acceptance:** After a day of use, `vault/daily/2026-MM-DD.md` exists with a clean log. Open in Obsidian; renders correctly. Disabling the handler stops vault writes; re-enabling resumes them.

### Phase 6: Skills with Progressive Disclosure

**Goal:** Skills can be defined as folders. Descriptions load at startup; bodies load on demand. Classifier can suggest a skill in its JSON output. v0.1 ships one skill.

**Tasks:**
- Implement `skills.py`: discover skills in `config/skills/`, parse `SKILL.md` frontmatter only at startup. Body is loaded on demand and cached per skill.
- Extend `classifier.py`: include the list of registered skill names and descriptions in the classifier prompt; expect a `skill` field in JSON output.
- Extend `router.py`: when a skill is selected (by command, classifier output, or `/skill <name>`), load the body and pass it to the context builder. The context builder appends it as `## Active Skill: <name>` after the persona section.
- Implement the `summarize-conversation` skill: triggers on `/summary`, summarizes the current session in 3-5 sentences using the operator persona.
- Add `/skills` command listing available skills (names + descriptions).
- Add `/reload` command that re-reads `UBONGO.md`, personas, skill metadata, and clears the skill body cache.

**Acceptance:** `/summary` produces a coherent summary of the current session. `/skills` lists `summarize-conversation`. Editing the skill's `SKILL.md` body and running `/reload` reflects the change without restart. Verifying that the skill body is *not* in the system prompt for messages where the skill isn't triggered (inspect the `before_llm` payload).

### Phase 7: Notification Queue and Delivery Policy

**Goal:** Every outbound message flows through the queue. Delivery is governed by quiet hours and ad-hoc holds. Slash commands manage holds. The policy engine is registered as a `before_send` handler.

Build in sub-steps; test each.

**Sub-step 7a: Queue and immediate delivery.**
- Implement `delivery/queue.py`: `enqueue(content, urgency, source, ...)`, `dequeue_deliverable() -> List[Item]`, `mark_delivered(id)`.
- Implement `delivery/worker.py`: asyncio task. Polls every `worker_poll_seconds`. Wakes immediately on `urgent` enqueue.
- Refactor existing bot response path: enqueue at urgency `urgent`, source `response`. Telegram send happens in the worker after `before_send` event passes.

*Acceptance:* Bot still feels instant. All Telegram sends go through the queue (verifiable in DB).

**Sub-step 7b: Policy engine as event handler.**
- Implement `delivery/policy.py`: function `effective_threshold(now) -> Urgency` consulting quiet hours and active `delivery_policy` rows. Function `can_deliver(item, now) -> bool`.
- Register `policy_check` as the default `before_send` handler. If it returns a payload with `deliver=False`, the worker leaves the item in the queue and updates `deliver_after`.

*Acceptance:* Set quiet hours to current time. Send a message; response is enqueued at `normal`-equivalent (not urgent for synchronous responses; this needs handling — see note below) and held. Disable quiet hours; response delivers.

*Note on synchronous responses and quiet hours:* synchronous responses to direct user messages are enqueued at `urgent` so they break through quiet hours. Otherwise the user would send a message during quiet hours and get no reply, which is broken. Only proactive messages (v0.3) get held by quiet hours. Document this clearly.

**Sub-step 7c: Slash commands.**
- Implement `delivery/commands.py`: handlers for `/hold`, `/resume`, `/quiet`, `/queue`.

*Acceptance:* `/hold 1m`, send a `low`-urgency test item via debug, verify it's held. After 1 minute, item delivers.

**Sub-step 7d: Catch-up summarizer.**
- Implement `delivery/catchup.py`. On `/resume` or hold expiry with >= `summarize_threshold` pending items, generate one summary message via the casual persona.

*Acceptance:* Hold for an hour, manually inject 3 fake queue items, `/resume`, get a single summary.

**Sub-step 7e: Notification control via natural language.**
- Add `notification_control` intent to the classifier prompt.
- When detected, route to the delivery commands handler. Confirm in plain language. Fall back to slash command suggestions on ambiguity.

*Acceptance:* "hold notifications for 2 hours" creates a 2-hour hold with confirmation. "stop bothering me until 6pm" creates a hold until 18:00.

**Sub-step 7f: Hold-until-ack safety.**
- Worker checks for `hold_until_ack` policies older than `hold_until_ack_warning_hours`. Sends single urgent ping.

*Acceptance:* `/hold`, advance the policy's `created_at` to 25 hours ago in SQLite, observe the worker sends the warning ping on its next cycle.

## Acceptance Criteria for v0.1 Complete

You are done when all of the following are true:

1. The bot responds to your Telegram messages and ignores everyone else.
2. Manual `/architect`, `/operator`, `/casual` commands work and feel different.
3. In `/auto` mode, persona is selected automatically and feels mostly right.
4. You can correct auto-selection with a slash command.
5. `UBONGO.md` is loaded for every persona; editing it changes behavior across all personas after `/reload`.
6. Conversation context persists across bot restarts within a session.
7. New session starts after 30 minutes of inactivity.
8. Compaction kicks in past the configured threshold; older history is replaced by a summary in recall; the summary is persisted and not regenerated each turn.
9. Daily notes write to the Obsidian vault and render correctly.
10. The `summarize-conversation` skill works via `/summary`. Skill bodies are *not* loaded until activation (verifiable in logs or memory inspection).
11. `/reload` picks up edits to `UBONGO.md`, personas, and skill metadata without restart.
12. Every outbound message goes through the notification queue.
13. Quiet hours hold proactive (non-synchronous) messages of insufficient urgency. Synchronous responses break through.
14. `/hold`, `/resume`, `/quiet`, `/queue` all work as specified.
15. Natural-language hold instructions are recognized and confirmed.
16. Catch-up after release produces a single summary, not a flood.
17. Named events fire at all the documented points; default handlers do their work; registering a no-op handler doesn't break the flow.
18. The system survives a full day of real use without crashing.
19. Total project size is under 3000 lines of Python (excluding tests). Hierarchical context, events, and progressive disclosure justify the bump from 2500.

If line 19 is failing, you've over-built. Cut.

## Out of Scope (v0.1) - Explicit Reminder

Deferred deliberately. Don't sneak them in.

- Slack, WhatsApp, Discord, web UI, voice
- Master Agent, worker agents, agent lifecycle management
- Parallel agent execution, debate mode, competitive mode, speculative execution
- Genetic Programming, runtime self-modification, prompt evolution
- Embedding-based memory recall, semantic search
- Topic-aware or persona-specific compaction strategies (the seam exists; the implementations come later)
- Bidirectional Markdown sync (vault is write-only in v0.1)
- Approval gates beyond text confirmation
- Sandboxing, tool execution (zero tools in v0.1), MCP server integration
- Multi-user support, RBAC, team features
- Self-healing workflows, retry orchestration beyond single retries
- Observability dashboards, distributed tracing
- Docker, Kubernetes, FastAPI, Redis, Qdrant, Memgraph, Temporal, NATS
- Scheduler / cron-style jobs (v0.3)
- External integrations: calendar, email, Reddit, news (v0.2 onward, one at a time)

---

## v0.2 Sketch (Not Part of v0.1 Build)

After two weeks of v0.1 use, prioritize from this list. Pick at most two.

- **CLI front-end.** Same router, same memory, same queue, same events. Different transport.
- **First external integration: Google Calendar.** OAuth flow, token storage in SQLite, one skill `calendar-review` activating on `/calendar` or calendar-related questions. Still request-response; no scheduler yet. First real tool: a CLI script `ubongo-calendar` invoked via a `bash` tool exposed to the LLM.
- **Embedding-based recall.** Add `sqlite-vec`. Embed each message at write time. Register an `after_recall` handler that augments history with semantically relevant past messages. The default compaction handler stays in place.
- **Topic-aware compaction.** Replace the default compaction function with one that detects topic shifts and summarizes per-topic. Swap in via config.
- **Structured fact extraction.** Populate the `facts` table. An `after_llm` handler runs over user messages and proposes facts. Surfaced via an `after_recall` handler when relevant.
- **Bidirectional vault sync.** A file watcher on `vault/` detects edits and ingests them as proposed updates to SQLite, mediated by a confirmation prompt.
- **A fourth persona.** Critic, Researcher, or Private. Only add if you've felt the lack.

## v0.3 Sketch (Not Part of v0.1 Build)

The scheduler. Adds proactive behavior on top of v0.2's integrations.

- **`config/jobs.yaml`** declares scheduled jobs:

```yaml
jobs:
  - name: morning_calendar_brief
    schedule: "0 7 * * *"
    skill: calendar-review
    persona: operator
    delivery:
      urgency: normal
    enabled: true

  - name: friday_inbox_triage
    schedule: "0 16 * * 5"
    skill: email-triage
    persona: architect
    delivery:
      urgency: low
    enabled: true
```

- **Job runner** uses APScheduler (in-process, cron syntax). Wakes when due, invokes the skill with the persona, enqueues the result with the configured urgency. Same delivery worker, same `before_send` handlers.

- **Urgency rules** in `config/urgency.yaml`:

```yaml
rules:
  - source: calendar
    when: starts_within_minutes <= 15
    urgency: urgent
  - source: email
    when: from_in: ["dalibor@kiwi.com"]
    urgency: normal
  - source: news
    urgency: low
```

- **Job management:** `/jobs`, `/job enable <name>`, `/job disable <name>`, `/job run <name>`.

- **Additional integrations as separate skills:** `email-triage`, `news-digest`, `reddit-research`. Each is a skill folder. Each backing capability is a CLI script the agent invokes via `bash`, not a first-class tool.

The reason this is v0.3 and not v0.1: the queue, policy engine, events, and progressive disclosure from v0.1 mean the scheduler is mostly "enqueue stuff on a cron." The hard parts are already solved.

---

## Setup Instructions (for the README)

```bash
# prerequisites
# - Python 3.11+
# - uv (https://docs.astral.sh/uv/)
# - A Telegram bot token from @BotFather
# - An OpenRouter API key (https://openrouter.ai/)

git clone <repo>
cd ubongo
uv sync

# configure
cp .env.example .env
# edit .env with your tokens
# edit config/settings.yaml: set telegram.allowed_user_ids
# (find your numeric Telegram ID by messaging @userinfobot)
# edit config/UBONGO.md if you want to customize identity/preferences
# edit config/personas/*.md to tune voices

# run
uv run python -m ubongo
```

---

## Final Notes for Claude Code

- Build phase by phase. Do not start Phase N+1 before Phase N is acceptance-tested. Use the acceptance criteria literally.
- Keep modules small. If a file exceeds 300 lines, consider splitting.
- Write tests for the queue, policy engine, compaction function, event dispatcher, and progressive-disclosure skill registry. The rest can rely on manual testing in v0.1.
- No new dependencies beyond those listed in the tech stack table without justification.
- The user's preferences apply to bot output too: prose over bullets, no em-dashes, no emojis, direct tone. These live in `config/UBONGO.md` and are inherited by every persona.
- Skills are loaded by description only at startup; bodies load on activation. Verify this before claiming Phase 6 complete.
- New behavior in v0.2+ ships as event handlers, not core-loop edits.
- New capabilities in v0.2+ default to CLI scripts invoked via `bash`, not first-class tools.
- When in doubt, defer. Anything not explicitly required for v0.1 acceptance criteria is out of scope for v0.1.
