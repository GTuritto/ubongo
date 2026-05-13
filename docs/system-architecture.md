# Ubongo System Architecture (Current Implementation)

This document describes the current codebase state (through Phase 9 in `STATUS.md`), focusing on runtime flow, subsystem boundaries, and persistent data model.

Diagram source file (editable in draw.io):
- [system-architecture.drawio](./diagrams/system-architecture.drawio)

## 1) Runtime Components

Draw.io page: `Runtime Components`

Summary:
- CLI (`__main__.py`, `repl.py`, `oneshot.py`) enters through `MasterAgent`.
- `MasterAgent` handles classify/plan/execute/govern/compose and persistence seams.
- `WorkflowRunner` dispatches worker agents (`research`, `persona:*`, `memory`).
- Queue and event bus coordinate side effects (`before_send`, `after_send`).
- SQLite store is canonical memory; vault is projected markdown.

```mermaid
flowchart LR
    CLI["CLI: main, repl, oneshot"] --> MASTER["MasterAgent: master.py"]
    MASTER --> CLASSIFIER["Classifier: classifier.py"]
    MASTER --> ROUTER["Router: router.py"]
    MASTER --> SKILLS["Skills Registry: skills.py"]
    MASTER --> RUNNER["WorkflowRunner: runner.py"]
    RUNNER --> AGENTS["Agents: Research, Persona, Memory"]
    CLASSIFIER --> LLM["LiteLLM/OpenRouter: llm.py"]
    AGENTS --> LLM
    MASTER --> STORE["SQLite Store: memory/store.py"]
    RUNNER --> STORE
    MASTER --> QUEUE["Delivery Queue: delivery/queue.py"]
    QUEUE --> EVENTS["Event Bus: events.py"]
    EVENTS --> VAULT["Vault Projection: memory/vault.py"]
    STORE --> EVENTS
    EVENTS --> COMPACT["Compaction: memory/compaction.py"]
    COMPACT --> STORE
```

## 2) End-to-End Turn Flow

Draw.io page: `Turn Flow`

Flow:
1. User input (REPL or one-shot)
2. `MasterAgent.handle()` classify + plan
3. Persist user turn + insert `workflow_runs` (`in_progress`)
4. `WorkflowRunner.execute()` agent dispatch
5. Persist assistant turn + governance decision + workflow outcome update
6. Enqueue response + `before_send`
7. Print response to terminal
8. `flush_delivered()` -> `after_send` -> vault projection -> mark delivered

```mermaid
sequenceDiagram
    participant User as User
    participant CLI
    participant Master
    participant Store
    participant Runner
    participant Agents
    participant Queue
    participant Events
    participant Vault

    User->>CLI: input text
    CLI->>Master: handle(message, persona, auto_mode, pending_skill)
    Master->>Master: classify() and plan()
    Master->>Store: current_or_new_conversation()
    Master->>Store: append user message
    Master->>Store: append workflow_run(in_progress)
    Master->>Runner: execute(workflow, context, message)
    Runner->>Store: recall()
    Runner->>Agents: run each workflow agent
    Agents-->>Runner: AgentResult
    Runner->>Store: append agent_runs
    Runner-->>Master: WorkflowResult
    Master->>Store: commit assistant message
    Master->>Store: append governance_decision
    Master->>Store: update workflow_run outcome
    Master->>Queue: enqueue_for_delivery()
    Queue->>Events: before_send
    Master-->>CLI: Response(text, token)
    CLI->>Queue: flush_delivered(token)
    Queue->>Events: after_send
    Events->>Vault: append daily note
    Queue->>Store: mark_delivered
    CLI-->>User: print response
```

## 3) Events and Side Effects

Draw.io page: `Events and Side Effects`

Key event chains:
- `store.recall()` -> `after_recall` -> `memory.compaction.maybe_compact()`
- `queue.flush_delivered()` -> `after_send` -> `MemoryAgent.project_vault()` -> daily note append

```mermaid
flowchart LR
    RECALL["store.recall()"] --> AFTER_RECALL["after_recall event"]
    AFTER_RECALL --> COMPACTION["memory.compaction.maybe_compact()"]
    COMPACTION --> SUMMARIES["persist summaries row"]

    FLUSH["queue.flush_delivered()"] --> AFTER_SEND["after_send event"]
    AFTER_SEND --> PROJECT["MemoryAgent.project_vault()"]
    PROJECT --> DAILY["vault/daily/YYYY-MM-DD.md"]
```

## 4) SQLite Data Model

Draw.io page: `SQLite Data Model`

Core operational tables:
- `conversations`, `messages`, `summaries`, `sessions`
- `workflow_runs`, `agent_runs`, `governance_decisions`
- `notification_queue`

Future-phase tables already present in schema:
- `facts`, `evolution_lineage`, `evolution_evaluations`, `pending_promotions`, `active_evolutions`, `vault_links`

```mermaid
erDiagram
    CONVERSATIONS ||--o{ MESSAGES : has
    CONVERSATIONS ||--o{ SUMMARIES : has
    CONVERSATIONS ||--o{ WORKFLOW_RUNS : has
    WORKFLOW_RUNS ||--o{ AGENT_RUNS : has
    WORKFLOW_RUNS ||--o{ GOVERNANCE_DECISIONS : has
    CONVERSATIONS ||--o| SESSIONS : current_session

    CONVERSATIONS {
        INT id PK
        TIMESTAMP started_at
        TIMESTAMP ended_at
        TEXT active_persona
    }
    MESSAGES {
        INT id PK
        INT conversation_id FK
        TEXT role
        TEXT content
        TIMESTAMP timestamp
        TEXT persona
        TEXT model
        INT tokens_in
        INT tokens_out
    }
    SUMMARIES {
        INT id PK
        INT conversation_id FK
        INT covers_from_message_id
        INT covers_to_message_id
        TEXT content
        TEXT strategy
        TIMESTAMP created_at
    }
    WORKFLOW_RUNS {
        INT id PK
        INT conversation_id
        INT message_id
        TEXT execution_mode
        TEXT outcome
        TIMESTAMP started_at
        TIMESTAMP ended_at
    }
    AGENT_RUNS {
        INT id PK
        INT workflow_run_id FK
        TEXT agent
        TEXT model
        INT tokens_in
        INT tokens_out
        INT latency_ms
        TEXT outcome
    }
    GOVERNANCE_DECISIONS {
        INT id PK
        INT workflow_run_id FK
        TEXT intent
        TEXT risk
        FLOAT confidence
        TEXT action
        TIMESTAMP decided_at
    }
    SESSIONS {
        INT user_id PK
        TIMESTAMP last_message_at
        TEXT active_persona
        INT current_conversation_id FK
        INT auto_mode
    }
```

## 5) Workflow + Agent Model

Workflows are configured in `config/workflows.yaml` and routed by `config/routing.yaml`.

Current execution mode implemented: `sequential`.

Runtime pattern:
- `classifier.classify()` -> `router.route_workflow()` + hysteresis
- Workflow template resolves to ordered agent list
- `WorkflowRunner` executes agents in order and persists `agent_runs`

```mermaid
flowchart LR
    C["Classification: intent, tone, risk, confidence"] --> ROUTE["router.route_workflow()"]
    ROUTE --> HYST["router.apply_hysteresis()"]
    HYST --> WF["Workflow: persona, model, skill, mode, agents"]
    REG["default_registry()"] --> RUN["WorkflowRunner.execute()"]
    WF --> RUN
    RUN --> OUT["WorkflowResult and agent_runs rows"]
```

## 6) Memory, Recall, and Compaction

Operational behavior:
- Recall returns recent messages + latest summary (or inherited summary from another conversation).
- `after_recall` can trigger compaction when thresholds are exceeded.
- Compaction persists cumulative summaries that preserve long-horizon facts beyond recall window.

```mermaid
flowchart TD
    TURN["New user turn"] --> RECALL2["store.recall(conversation_id)"]
    RECALL2 --> MSGS["recent messages"]
    RECALL2 --> SUM["latest summary or inherited summary"]
    RECALL2 --> AR_EVT["after_recall event"]
    AR_EVT --> MAYBE["compaction.maybe_compact()"]
    MAYBE --> CHECK["threshold checks"]
    CHECK -->|compact| WRITE["persist summary row"]
    CHECK -->|skip| DONE["no-op"]
```

## 7) REPL Command Surface

Implemented command families in `repl.py`:
- Persona and mode: `/architect`, `/operator`, `/casual`, `/auto`
- Skills/meta: `/skill <name>`, `/skills`, `/summary`, `/reload`
- Observability: `/queue [N]`, `/decisions [N]`, `/agents`
- Control: `/exit`

## 8) Prompt and Configuration Hierarchy

Prompt assembly layers:
1. `config/UBONGO.md` (global identity)
2. `config/personas/*.md` (persona overlay)
3. `config/skills/<name>/SKILL.md` (active skill body, when used)
4. Agent-role framing (worker-specific instructions)

Skill activation templates are loaded from `config/skills/<name>/prompts/*.md` for skill-specific user messages (for example `/summary`).

```mermaid
flowchart TD
    UBONGO["config/UBONGO.md"] --> SYSTEM["final system prompt"]
    PERSONA["config/personas/*.md"] --> SYSTEM
    SKILL["config/skills/[name]/SKILL.md"] --> SYSTEM
    ROLE["agent role frame"] --> SYSTEM
    TEMPLATE["config/skills/[name]/prompts/*.md"] --> USERMSG["skill-specific user message"]
```

---

When runtime architecture changes, update this document and the draw.io file together.
