# Flow Diagram + UML Sequence — one turn, end to end

How a single user message moves through Ubongo: the **flow diagram** (control
flow with the governance branch and the background daemons) and the **UML
sequence diagram** (the same turn as message passing between components over
time). Both reflect the real pipeline in `master.handle`:
`classify → plan → execute → govern → compose → commit → enqueue`.

## Flow diagram

```mermaid
flowchart TD
    IN([User input: REPL / one-shot]) --> H["MasterAgent.handle()"]

    H --> CL["classify<br/>classifier.py + LLM"]
    CL --> PL["plan<br/>router.plan_workflow → WorkflowPlan → Workflow"]
    PL --> EX["execute<br/>WorkflowRunner"]

    EX --> MODE{execution_mode}
    MODE -->|sequential| SEQ["A → B → C<br/>+ full Repair ladder on failure"]
    MODE -->|"parallel · competitive · collaborative · debate · speculative"| FAN["fan-out via asyncio.gather<br/>Repair = peer replacement only"]
    SEQ --> RES["WorkflowResult<br/>composer agent's text + evaluator confidence"]
    FAN --> RES

    RES --> GOV["govern<br/>decision matrix (risk · confidence · reversibility)"]
    GOV --> ACT{action}
    ACT -->|auto| CMP["compose<br/>last composer agent's text"]
    ACT -->|"reject · ask_clarification"| MSG["canned gated message"]
    ACT -->|require_approval| APR{"Approve?<br/>y / n / why"}
    APR -->|y → re-issue approved=True| CMP
    APR -->|n| ABORT([aborted — nothing delivered])

    CMP --> COMMIT["commit-or-drop<br/>WriteBuffer: commit on ok, drop on failure"]
    MSG --> COMMIT
    COMMIT --> Q["enqueue<br/>notification_queue"]
    Q --> DEL["dequeue → before_send → stdout → after_send"]
    DEL --> MEM["Memory Agent commits<br/>SQLite + vault + embeddings (single writer)"]
    MEM --> OUT([response shown to user])

    subgraph daemons["Background daemons — started by the REPL, paused/off by default"]
      direction LR
      GP["GP loop<br/>evolve prompts/config → /improvements"]
      VW["Vault watcher<br/>ingest external vault edits"]
      AU["Authoring daemon<br/>draft new skills → quarantine → /skill-candidates"]
    end
    H -. "runs alongside (never on the turn path)" .- daemons
```

Notes:

- **No bypass.** Every turn flows through `MasterAgent.handle`; there is no path
  from the CLI to an agent that skips classify/plan/govern.
- **Governance branch.** `require_approval` does not deliver automatically — the
  REPL prompts `y/n/why`; `y` re-issues the turn with `approved=True` (which
  overrides the gate to `auto`); `n` aborts. One-shot mode prints the gated
  message and exits non-zero (no interactive approval).
- **Commit-or-drop.** The turn body runs inside a `WriteBuffer`; the assistant
  message commits only on success, so a failed turn leaves no partial state.
- **Every event fires.** `before_/after_` hooks bracket each stage
  (`before_classify`, `after_execute`, `before_compose`, …); v0.2+ behavior
  registers on these rather than editing the Master.

## UML sequence diagram

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant CLI as CLI (REPL / one-shot)
    participant M as MasterAgent
    participant C as Classifier
    participant R as Router
    participant WR as WorkflowRunner
    participant A as Worker Agent(s)
    participant G as Governance
    participant Q as Notification Queue
    participant Mem as Memory Agent
    participant LLM as LLM Gateway

    User->>CLI: message
    CLI->>M: handle(message)

    M->>C: classify(message, context)
    C->>LLM: complete (classifier model)
    LLM-->>C: {intent, tone, task_type, risk, suggested_skill, confidence}
    C-->>M: Classification

    M->>R: plan_workflow(classification)
    R-->>M: WorkflowPlan → Workflow (persona + agents + mode)

    M->>WR: execute(workflow, context)
    loop each agent, per execution-mode strategy
        WR->>A: run(AgentInput, Context)
        A->>LLM: complete (agent model)
        LLM-->>A: text
        A-->>WR: AgentResult (text, ok, confidence, tokens)
    end
    WR-->>M: WorkflowResult (composer text + evaluator confidence)

    M->>G: decide(classification, workflow, result)
    G-->>M: Decision (auto · ask_clarification · require_approval · reject)

    alt action == require_approval
        M-->>CLI: Response.approval (summary + why)
        CLI->>User: Approve? (y/n/why)
        User-->>CLI: y
        CLI->>M: handle(message, approved=True)
        Note over M: re-issue overrides the gate to auto
    end

    M->>Q: enqueue(response, urgency=urgent)
    Q-->>CLI: dequeue + deliver
    CLI-->>User: response

    M->>Mem: commit(turn)
    Mem->>Mem: SQLite + vault projection + embeddings (single writer)
```

The sequence is the same pipeline as the flow diagram, drawn as message passing.
The repair ladder (on `agent_failed`) and the `before_/after_` events are elided
here for readability — see [c4-dynamic-turn.md](c4-dynamic-turn.md) for the
event-annotated trace and [c4-components-orchestration.md](c4-components-orchestration.md)
for the Master + Runner internals.
