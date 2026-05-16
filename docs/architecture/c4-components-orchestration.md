# C4 Level 3 — Component Diagram: Orchestration

This drills into the Master Agent and Workflow Runner: how a classified turn
becomes a dispatched fleet of worker agents and a governed, persisted result.

```mermaid
C4Component
  title Component Diagram - Orchestration (Master Agent + Workflow Runner)

  Container(cli, "CLI", "Python", "REPL / one-shot")
  ContainerDb(db, "SQLite Database", "SQLite", "workflow_runs, agent_runs, governance_decisions, repair_runs")

  Container_Boundary(master, "Master Agent") {
    Component(pipeline, "Turn Pipeline", "master.handle", "classify -> plan -> execute -> decide -> compose -> commit -> enqueue")
    Component(composer, "Composer", "Python", "Picks WorkflowResult.text from the last agent with composer=True")
    Component(commit, "Run Committer", "Python", "Persists workflow_runs + agent_runs + governance_decisions")
  }

  Container_Boundary(runner, "Workflow Runner") {
    Component(dispatch, "Mode Dispatch", "runner.execute", "Selects a strategy coroutine off workflow.execution_mode")
    Component(seq, "Sequential Strategy", "asyncio", "Threads prior_findings forward; drives Repair retries")
    Component(fanout, "Fan-out Strategies", "asyncio.gather", "parallel / competitive / collaborative / debate / speculative")
  }

  Component(classifier, "Classifier", "Python", "LLM intent / tone / risk / skill")
  Component(router, "Router", "Python", "classification -> Workflow")
  Component(governance, "Governance", "Python", "Decision matrix")
  Component(repair, "Repair Agent", "Python", "Failure taxonomy + recovery ladder")
  Component(fleet, "Worker Agent Fleet", "Python", "Research, Coding, Evaluator, Critic, Execution, Persona")
  Component(bus, "Event Bus", "Python", "before_*/after_* hooks")

  Rel(cli, pipeline, "handle(turn)")
  Rel(pipeline, classifier, "1. Classify")
  Rel(pipeline, router, "2. Plan workflow")
  Rel(pipeline, dispatch, "3. Execute")
  Rel(dispatch, seq, "mode = sequential")
  Rel(dispatch, fanout, "mode = parallel / debate / ...")
  Rel(seq, fleet, "Dispatch agents in order")
  Rel(fanout, fleet, "Dispatch agents concurrently")
  Rel(seq, repair, "On agent failure: plan_recovery")
  Rel(fanout, repair, "On agent failure: replace_with_peer")
  Rel(pipeline, governance, "4. Decide")
  Rel(pipeline, composer, "5. Compose")
  Rel(pipeline, commit, "6. Commit")
  Rel(commit, db, "Persist runs", "sqlite3")
  Rel(pipeline, bus, "Dispatch lifecycle events")

  UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```

## How it works

The **Turn Pipeline** (`master.handle`) is the one orchestration seam. It runs a
fixed sequence and there is no bypass path:

1. **Classify** — the Classifier returns intent, tone, task type, suggested
   skill, risk, and a confidence score for the user message.
2. **Plan** — the Router maps that classification, via `routing.yaml` and
   `workflows.yaml`, to a `Workflow`: a persona, model, optional skill,
   execution mode, and an ordered tuple of agent names.
3. **Execute** — the Workflow Runner's **Mode Dispatch** picks a strategy
   coroutine off `workflow.execution_mode`. The runner is async internally and
   sync externally, so the Master stays synchronous.
4. **Decide** — Governance applies the decision matrix
   (`auto` / `ask_clarification` / `require_approval` / `reject`).
5. **Compose** — the result text comes from the last agent whose class declares
   `composer = True`. Validators (Evaluator, Critic) and helpers (Research,
   Execution) contribute `prior_findings` but never claim the response.
6. **Commit** — the Run Committer persists `workflow_runs`, `agent_runs`, and
   `governance_decisions`. Tracing is not optional.
7. **Enqueue** — the response goes to the Notification Queue (see the container
   diagram).

## Execution modes

Mode Dispatch selects one of six strategies:

| Mode | Status | Behavior |
|------|--------|----------|
| `sequential` | Active | Threads `prior_findings` forward agent to agent; drives the Repair recovery ladder. |
| `parallel` | Active | `asyncio.gather` fan-out; agents see no `prior_findings`. |
| `competitive` | Phase 12 | Multiple agents, best result wins. |
| `collaborative` | Phase 12 | Agents refine a shared draft. |
| `debate` | Phase 12 | Adversarial multi-round exchange. |
| `speculative` | Phase 12 | Fast draft plus verification. |

## Repair

The **Repair Agent** does not run as a workflow step. It is consulted
synchronously by the runner when an agent fails. It classifies the failure
(`FailureKind`) and walks an ordered strategy ladder: retry with a variant
prompt, retry with a different model, retry with a smaller model and shorter
prompt, or replace the failed agent with a peer. Sequential mode walks the full
ladder; fan-out modes act only on `replace_with_peer`. Every attempt is logged
to `repair_runs`.
