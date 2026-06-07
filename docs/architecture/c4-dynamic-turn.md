# C4 Dynamic — A Single Turn

This traces one user message end to end through the orchestration pipeline.

```mermaid
C4Dynamic
  title Dynamic Diagram - Processing One Turn

  Person(user, "Giuseppe", "The single user")
  Component(cli, "CLI", "Python", "REPL / one-shot")
  Component(master, "Master Agent", "Python", "Orchestration seam")
  Component(classifier, "Classifier", "Python", "Intent / risk classification")
  Component(router, "Router", "Python", "classification -> Workflow")
  Component(runner, "Workflow Runner", "Python", "Mode strategies")
  Component(fleet, "Worker Agent Fleet", "Python", "Research, Persona, etc.")
  Component(governance, "Governance", "Python", "Decision matrix")
  Component(memory, "Memory Agent", "Python", "Single durable writer")
  Component(queue, "Notification Queue", "Python", "Outbound seam")

  Rel(user, cli, "1. Types a message")
  Rel(cli, master, "2. handle(turn)")
  Rel(master, classifier, "3. Classify message", "LLM call")
  Rel(master, router, "4. plan_workflow -> WorkflowPlan (master adds model + skill)")
  Rel(master, runner, "5. Execute workflow")
  Rel(runner, fleet, "6. Dispatch agents", "per execution mode")
  Rel(runner, master, "7. Return WorkflowResult")
  Rel(master, governance, "8. Decide: auto / ask / approve / reject")
  Rel(master, memory, "9. Commit message + runs", "WriteBuffer")
  Rel(master, queue, "10. Enqueue composed response")
  Rel(queue, cli, "11. Deliver response")
  Rel(cli, user, "12. Print reply")

  UpdateRelStyle(user, cli, $textColor="blue", $offsetY="-20")
  UpdateRelStyle(master, classifier, $textColor="green", $offsetX="-40")
  UpdateRelStyle(runner, fleet, $textColor="orange", $offsetY="-10")
  UpdateRelStyle(master, governance, $textColor="red", $offsetX="-30")
  UpdateRelStyle(queue, cli, $textColor="blue", $offsetY="10")
```

## Step-by-step

1-2. The user types a message; the CLI hands it to `master.handle`.

3. **Classify.** One LLM call returns intent, tone, task type, suggested skill,
   risk, and confidence.

4. **Plan.** `router.plan_workflow` maps the classification, through `routing.yaml`
   and `workflows.yaml`, to a validated `WorkflowPlan` (persona, agents with the
   evaluator appended, mode, rounds, timeout) — validating the mode/agents shape at
   plan time. `master.plan` adds the persona model and resolved skill to make the
   `Workflow` (ADR-0012).

5-6. **Execute.** The Workflow Runner selects the strategy coroutine for the
   workflow's execution mode and dispatches the agent fleet — in order for
   `sequential`, concurrently for fan-out modes. If an agent fails, the runner
   consults the Repair Agent before giving up.

7. The runner returns a `WorkflowResult`. Its `text` is taken from the last
   agent with `composer = True`.

8. **Decide.** Governance applies the decision matrix. A `require_approval`
   action pauses here and the CLI prompts the user for y/n before continuing;
   `reject` ends the turn with an apology.

9. **Commit.** The Master calls the Memory Agent, which stages the assistant
   message plus `workflow_runs` / `agent_runs` / `governance_decisions` rows in
   the WriteBuffer and commits them atomically.

10-12. **Enqueue and deliver.** The composed response is enqueued in the
   Notification Queue — every outbound message goes through it, even synchronous
   CLI replies — then the CLI dequeues and prints it.

## Lifecycle events

At each pipeline boundary the Master dispatches a `before_*` / `after_*` event
on the Event Bus (`before_classify`, `after_execute`, `agent_failed`,
`before_send`, ...). v0.2+ behavior registers handlers on these events rather
than editing the pipeline, so the steps above stay stable as the system grows.
