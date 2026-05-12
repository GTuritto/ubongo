# Phase 9 — First Workers (Research + Memory): Implementation Plan

Date: 2026-05-12
Branch: `phase-9-research-memory` (off `main` at `c26cec9`)
Spec source: [UBONGO_BUILD.md](../UBONGO_BUILD.md) §Phase 9 (lines 934–964), Worker Agents table (105–129), Agent protocol (110–116), Workflow Runner (131–142), event payload table (300–319), `workflow_runs` + `agent_runs` schema (369–392), pipeline diagram (61–91).

## Goal

Real worker agents enter the system for the first time. Master Agent stops being the one thing that calls the LLM directly; instead, `MasterAgent.execute` delegates to a `WorkflowRunner` that runs the agents listed in `workflow.agents` in sequential order, each one producing an `AgentResult` and emitting `agent_started` / `agent_completed` / `agent_failed` events. The Research Agent retrieves relevant context (conversation history + vault snippets) and synthesizes findings for research-style turns. The Memory Agent becomes the only writer for the assistant message and the vault projection; other agents (and the runner itself) return findings, never persist them. `/agents` lists the registered workers. `agent_runs` gets populated per agent dispatch.

**No regression in single-agent (casual / technical / quick_action) flows.** Those still pass through one persona agent step, just now via the runner instead of inline. Same prompts, same model, same output shape; the only observable diff is a richer `workflow.agents` tuple and one or two extra `agent_runs` rows per turn.

## Why this plan exists

Three patterns Phase 9 locks in that the next several phases inherit:

1. **The Agent protocol becomes the unit of work.** After Phase 9, `agents/base.py::Agent` is the seam every worker plugs into: Phase 10 Evaluator/Critic/Persona, Phase 11 Coding/Execution/Repair, Phase 12 modes (parallel/competitive/etc.). If the protocol is wrong, the next 4–5 phases pay for it. Keeping it minimal — `name`, `role`, `default_model`, `run(input, context) -> AgentResult` — is the right tradeoff for v0.1.
2. **The Workflow Runner is the only thing that calls agents.** No more inline `complete(system_prompt, history, ...)` in master.py. After Phase 9, `MasterAgent.execute` calls `runner.execute(workflow, ctx, message) -> WorkflowResult`; the runner orchestrates agent dispatch, history threading, and `agent_runs` writes. Phase 12 adds the other five execution modes by extending this single function; the call site doesn't move.
3. **Memory Agent owns the assistant-message write and the vault projection.** Spec is explicit: Memory is the single writer to durable memory. Phase 9 makes this real for the writes that currently exist in production code paths (assistant message into `messages`, vault note via `after_send`). The user message stays a Master responsibility (it's input, not agent output) and orchestration tables (`workflow_runs`, `agent_runs`, `governance_decisions`, `notification_queue`) stay with Master / runner / queue — those aren't durable memory, they're orchestration logs. Phase 11 will extend the rule to facts; Phase 20 to embeddings.

## Branch + commit strategy

Branch: `phase-9-research-memory` off `main` at `c26cec9` (Phase 8 merged). Seven commits matching the spec's sub-phase letters:

- **9a** — `agents/base.py`: `Agent` protocol, `AgentInput`, `AgentResult`. Tests.
- **9b** — `agents/research.py`: `ResearchAgent`. Retrieval helpers (conversation memory + vault snippets) + synthesis LLM call. Tests.
- **9c** — `agents/memory.py`: `MemoryAgent`. Owns assistant-message append + vault projection. Re-points the `after_send` vault handler through it. Tests.
- **9d** — `runner.py`: sequential workflow runner. `execute(workflow, ctx, message) -> WorkflowResult`. Dispatches `agent_started` / `agent_completed` / `agent_failed`. Writes `agent_runs`. Tests.
- **9e** — `master.py`: `plan` builds the right `agents` tuple per intent; `execute` delegates to runner. `workflows.yaml` minimal file introduced. Tests updated.
- **9f** — `/agents` REPL command + tests.
- **9g** — STATUS + smoke playbook Phase 9 section.

## Sub-phases

### 9a — Agent base (`src/ubongo/agents/base.py`)

**Purpose:** Define the protocol every worker implements and the input/output dataclasses the runner passes between them.

**Tasks:**

1. Create `src/ubongo/agents/base.py` with:

   ```python
   from __future__ import annotations
   from dataclasses import dataclass, field
   from typing import Protocol, runtime_checkable

   @dataclass(frozen=True)
   class AgentInput:
       message: str                       # the current user message
       history: tuple[dict, ...]          # prior turns, ending with the user message
       summary_text: str | None           # cross-session summary, if any
       prior_findings: tuple[str, ...]    # outputs from earlier agents in this workflow
       metadata: dict = field(default_factory=dict)

   @dataclass(frozen=True)
   class AgentResult:
       text: str                          # the agent's contribution (findings, response, etc.)
       ok: bool
       model: str | None
       tokens_in: int
       tokens_out: int
       latency_ms: int
       confidence: float | None = None    # set by Evaluator in Phase 10; None elsewhere
       metadata: dict = field(default_factory=dict)
       error: str | None = None           # populated when ok=False

   @runtime_checkable
   class Agent(Protocol):
       name: str          # e.g. "research", "memory", "persona:architect"
       role: str          # short human-readable description
       default_model: str # OpenRouter model id, or "" if no LLM call

       def run(self, input: AgentInput, context: "Context") -> AgentResult: ...
   ```

2. `Context` is `master.Context` (already exists). Imported lazily inside the protocol annotation to avoid a circular import; the protocol references it as a string forward-ref.

3. **Sync vs async.** Spec line 115 shows `async def run`. v0.1 stays **sync** in Phase 9: the runner is sequential, no `asyncio.gather`, and dragging async through master/repl/oneshot now buys nothing. Phase 12 (parallel / competitive / speculative) will retrofit. **Open question 1 below.**

4. Tests in `tests/test_agents_base.py` (~4 tests):
   - `AgentInput` and `AgentResult` are frozen dataclasses with the expected fields.
   - A toy class implementing the protocol passes `isinstance(toy, Agent)`.
   - A class missing `run` fails `isinstance`.
   - Default `metadata` is independent per instance (no shared dict bug).

**Files added:** `src/ubongo/agents/base.py`, `tests/test_agents_base.py`.

### 9b — Research Agent (`src/ubongo/agents/research.py`)

**Purpose:** Retrieve relevant context (recent conversation memory + vault daily-note snippets) and synthesize findings for the persona to compose against.

**Tasks:**

1. Create `src/ubongo/agents/research.py`:

   ```python
   from __future__ import annotations
   import logging
   from dataclasses import asdict
   from pathlib import Path
   from ubongo import skills
   from ubongo.agents.base import AgentInput, AgentResult
   from ubongo.config import load_config
   from ubongo.context import build_system_prompt
   from ubongo.llm import LLMError, complete
   from ubongo.memory import store, vault

   logger = logging.getLogger("ubongo.agents.research")

   class ResearchAgent:
       name = "research"
       role = "retrieval and synthesis over conversation memory and the vault"
       default_model: str  # resolved from config in __init__

       def __init__(self) -> None:
           cfg = load_config()
           self.default_model = cfg["models"].get("research", cfg["models"]["architect"])
           self.max_tokens = int(cfg.get("agents", {}).get("research", {}).get("max_tokens", 800))

       def run(self, input: AgentInput, context) -> AgentResult: ...
   ```

2. `run()` does three steps:

   **(i) Retrieve conversation memory.** Pull last K (default 30) messages across all conversations via a new helper `store.last_n_messages_global(k)` (Phase 9-only helper; Phase 20 replaces with sqlite-vec). Filter by simple keyword overlap with the user message (tokenize on whitespace, lowercase, drop stopwords from a short builtin list of ~30 words, keep matches with ≥1 shared content word). Cap at 8 retrieved messages.

   **(ii) Retrieve vault snippets.** New helper `vault.search_daily_notes(query, max_snippets=5)`: walk `vault/daily/*.md` (latest 30 files by mtime), grep for any content word, return up to 5 `(path, snippet)` tuples with a 200-char window around the match. Hidden / non-md files skipped. If the vault dir doesn't exist, return `[]`.

   **(iii) Synthesize.** Build the Research system prompt:

   ```
   {build_system_prompt("operator", agent_role="research")}

   You are the Research Agent. Read the retrieved context below and produce a
   concise, neutral synthesis (max ~6 short paragraphs) of what is relevant to
   the user's question. Cite sources inline as [conv:<id>:msg:<id>] or
   [vault:<path>]. Do not answer in the user's voice; the persona agent will
   compose the final reply.

   ## Retrieved conversation messages

   {numbered messages, with [conv:<id>:msg:<id>] tags}

   ## Retrieved vault snippets

   {numbered snippets, with [vault:<relative-path>] tags}
   ```

   Call `complete(...)` with the user's message as the single user turn. Cap at `self.max_tokens`. Return an `AgentResult(text=findings, ok=True, model=..., tokens_in=..., tokens_out=..., latency_ms=..., metadata={"retrieved_messages": M, "retrieved_snippets": S})`.

3. **Failure handling.** LLMError → `AgentResult(text="", ok=False, error="research_llm_error", model=..., ...)`. The runner will see `ok=False`, dispatch `agent_failed`, and the next agent (the persona) still runs (Phase 9 has no fallback — empty prior_findings is fine; persona will answer directly). Phase 13 Repair will retry. Note as an explicit decision in the doc.

4. **No DB writes from ResearchAgent.** It only reads. Spec scenario 4 enforcement (see 9c).

5. **`build_system_prompt` extension.** Today it accepts `(persona, skill_name=None)`. Add an optional `agent_role: str | None = None` parameter. When set, prepend a short stanza `## Agent role: {role}` after the persona body. Cached the same way persona bodies are. Update `context.py` and existing call sites (none currently pass `agent_role`, so it's purely additive).

6. Tests in `tests/test_agents_research.py` (~6 tests):
   - Retrieves conversation messages by keyword overlap; stopwords excluded.
   - Returns empty snippet list when vault dir missing.
   - Returns up to 5 snippets, ordered by file mtime descending, with surrounding context.
   - `run()` happy path returns `AgentResult.ok=True` with non-empty text (LLM mocked).
   - On `LLMError`: returns `AgentResult(ok=False, error="research_llm_error")`, no exception escapes.
   - Default model resolves from settings; `agents.research.max_tokens` is honored.

**Files added:** `src/ubongo/agents/research.py`, `tests/test_agents_research.py`.
**Files modified:** `src/ubongo/memory/store.py` (`+last_n_messages_global`), `src/ubongo/memory/vault.py` (`+search_daily_notes`), `src/ubongo/context.py` (`+agent_role` param), `config/settings.yaml` (`+models.research`, `+agents.research.max_tokens`).

### 9c — Memory Agent (`src/ubongo/agents/memory.py`)

**Purpose:** Single writer for assistant-message persistence and vault projection. Other agents and the runner do not write to `messages` or the vault. The user message stays in Master (it's input, not an agent output).

**Tasks:**

1. Create `src/ubongo/agents/memory.py`:

   ```python
   from __future__ import annotations
   import contextvars
   import logging
   from datetime import datetime
   from ubongo import events
   from ubongo.agents.base import AgentInput, AgentResult
   from ubongo.memory import store, vault

   logger = logging.getLogger("ubongo.agents.memory")

   # Soft enforcement token. Set inside MemoryAgent writes; tests in production
   # config can opt into strict mode and have store helpers assert it's set.
   _writer_token: contextvars.ContextVar[bool] = contextvars.ContextVar(
       "ubongo_memory_writer", default=False
   )

   def assert_memory_writer() -> None:
       """Test-hook: production code never calls this; tests opt in via fixture."""
       if not _writer_token.get():
           raise RuntimeError("Direct write outside MemoryAgent (single-writer rule)")

   class MemoryAgent:
       name = "memory"
       role = "single writer for messages, summaries, facts, vault, embeddings"
       default_model = ""

       def run(self, input: AgentInput, context) -> AgentResult: ...
       def commit_assistant_turn(self, ...) -> int: ...
       def project_vault(self, payload: dict) -> None: ...
   ```

2. `run(input, context)` semantics: Memory Agent's `run` writes the assistant message from `input.metadata` (the runner places `response_text`, `model`, `tokens_in`, `tokens_out`, `persona`, `conversation_id` there). Returns `AgentResult(text="", ok=True, metadata={"assistant_message_id": <id>})`. The runner records the `agent_runs` row.

3. **`after_send` vault handler migrates here.** Today `memory/vault.py` calls `events.register("after_send", _after_send_handler)` at import time. Change: remove that registration line from `vault.py`; in `memory/__init__.py`, register a wrapper that takes the token, sets `_writer_token`, calls `vault.append_to_daily_note`, resets. Net effect: the same vault file is written by the same code, but now through a path that asserts the writer token is held.

4. **Soft enforcement, not hard.** v0.1 Phase 9 ships the `_writer_token` machinery and the assertion helper but does **not** wire `assert_memory_writer()` into existing store/vault functions in production. Spec scenario 4 ("test mode") is satisfied via a pytest fixture that monkeypatches `store.append_message` (assistant-role calls) and `vault.append_to_daily_note` to call `assert_memory_writer()` first. Test verifies: under the fixture, a synthetic Research-like writer that calls `store.append_message(...)` raises; the production MemoryAgent path passes. This avoids polluting production code with assertions until a real second writer threat exists (Phase 11 Coding/Execution). **Open question 2.**

5. Tests in `tests/test_agents_memory.py` (~5 tests):
   - `MemoryAgent.run()` writes the assistant message and returns `assistant_message_id`.
   - Vault `after_send` flows through MemoryAgent and the token is held during write.
   - Strict-mode fixture: a non-Memory caller into `store.append_message(role="assistant", ...)` raises.
   - Strict-mode fixture: the MemoryAgent path does not raise.
   - `assert_memory_writer()` is a no-op when called inside `with _writer_token` (the agent's own write path).

**Files added:** `src/ubongo/agents/memory.py`, `tests/test_agents_memory.py`.
**Files modified:** `src/ubongo/memory/vault.py` (remove the `events.register` line), `src/ubongo/memory/__init__.py` (register the MemoryAgent-mediated `after_send` handler at module import).

### 9d — Workflow runner skeleton (`src/ubongo/runner.py`)

**Purpose:** The sole place that runs agents. Sequential mode only; Phase 12 adds the other five.

**Tasks:**

1. Create `src/ubongo/runner.py`:

   ```python
   from __future__ import annotations
   import logging
   import time
   from dataclasses import asdict
   from ubongo import events
   from ubongo.agents.base import Agent, AgentInput, AgentResult
   from ubongo.memory import store

   logger = logging.getLogger("ubongo.runner")

   class WorkflowRunner:
       def __init__(self, registry: dict[str, Agent]):
           self.registry = registry

       def execute(self, workflow, context, message, workflow_run_id: int | None = None
       ) -> "WorkflowResult":
           if workflow.execution_mode != "sequential":
               raise NotImplementedError(f"Phase 9: only sequential. Got {workflow.execution_mode}.")
           prior_findings: list[str] = []
           summary_text, history = _build_history(context.conversation_id, message)
           last_result: AgentResult | None = None
           for agent_name in workflow.agents:
               agent = self.registry[agent_name]
               input = AgentInput(
                   message=message,
                   history=tuple(history),
                   summary_text=summary_text,
                   prior_findings=tuple(prior_findings),
                   metadata={"persona": workflow.persona, "skill": workflow.skill_name},
               )
               started_at = store.now_iso()
               events.dispatch("agent_started", {"agent": agent_name, "input_message_len": len(message)})
               t0 = time.monotonic()
               try:
                   result = agent.run(input, context)
               except Exception as exc:
                   logger.warning("agent_exception", extra={"agent": agent_name, "cause": str(exc)})
                   result = AgentResult(text="", ok=False, model=getattr(agent, "default_model", ""),
                                        tokens_in=0, tokens_out=0,
                                        latency_ms=int((time.monotonic() - t0) * 1000),
                                        error=type(exc).__name__)
               ended_at = store.now_iso()
               if workflow_run_id is not None:
                   store.append_agent_run(
                       workflow_run_id=workflow_run_id,
                       agent=agent_name, model=result.model,
                       input={"message_len": len(message), "history_len": len(history),
                              "prior_findings": len(prior_findings)},
                       output={"text_len": len(result.text), "error": result.error},
                       confidence=result.confidence,
                       tokens_in=result.tokens_in, tokens_out=result.tokens_out,
                       latency_ms=result.latency_ms,
                       outcome="success" if result.ok else "failure",
                       started_at=started_at, ended_at=ended_at,
                   )
               if result.ok:
                   events.dispatch("agent_completed", {"agent": agent_name, "ok": True,
                                                       "tokens_in": result.tokens_in,
                                                       "tokens_out": result.tokens_out})
                   prior_findings.append(result.text)
                   last_result = result
               else:
                   events.dispatch("agent_failed", {"agent": agent_name, "error": result.error})
                   # Sequential v0.1: keep going. Phase 13 Repair will intervene.
           if last_result is None:
               return WorkflowResult(text=_LLM_FAILURE_MESSAGE, ok=False, tokens_in=0,
                                     tokens_out=0, model="", latency_ms=0)
           return WorkflowResult(text=last_result.text, ok=True, ...)
   ```

2. `_build_history` is the helper currently named `_build_message_history` in `master.py`. **Move it to runner.py** as a private helper (`master.py` no longer needs it). Same body.

3. **`store.append_agent_run`** is a new helper:

   ```python
   def append_agent_run(workflow_run_id: int, *, agent: str, model: str | None,
                        input: dict, output: dict, confidence: float | None,
                        tokens_in: int, tokens_out: int, latency_ms: int,
                        outcome: str, started_at: str, ended_at: str) -> int: ...
   ```

4. **Agent registry.** `runner.py` exposes a module-level `default_registry()` that imports and instantiates the three Phase-9 agents (Research, Memory, PersonaAgent-wrapper) plus a wrapper per persona. Persona wrappers (`PersonaAgent(persona_name)`) are minimal classes in `agents/personas.py` (existing file) that implement the protocol: `name = f"persona:{persona_name}"`, `role = "persona composer"`, `default_model = self._resolved`, `run(input, context)` calls the existing LLM with the persona's system prompt + `prior_findings` woven in as `## Research findings\n\n{findings}` if any. Phase 10 formalizes Persona Agents further; Phase 9 just wraps the existing single-LLM flow as an Agent.

5. **History rule.** The conversation history written for the persona MUST be identical to today's Phase 8 history for non-research turns (else we get a regression in casual_reply). Concretely: when `workflow.agents == ("persona:<x>",)`, the `prior_findings` tuple is empty, so the persona's system prompt is exactly what `master.execute` builds today. Verified by 9e's parity test.

6. Tests in `tests/test_runner.py` (~7 tests):
   - Sequential dispatch with one agent: returns that agent's `AgentResult.text`.
   - Sequential dispatch with two agents: second sees first's findings in `prior_findings`.
   - Each agent in workflow produces one `agent_runs` row when `workflow_run_id` provided.
   - `agent_started` / `agent_completed` dispatched in order.
   - `agent_failed` dispatched on `ok=False`; runner keeps going.
   - Unknown execution_mode raises `NotImplementedError`.
   - All agents fail → `WorkflowResult(ok=False, text=_LLM_FAILURE_MESSAGE)`.

**Files added:** `src/ubongo/runner.py`, `tests/test_runner.py`.
**Files modified:** `src/ubongo/memory/store.py` (`+append_agent_run`), `src/ubongo/agents/personas.py` (`+PersonaAgent` class wrapper).

### 9e — Master Agent picks Research + delegates to runner

**Purpose:** `MasterAgent.plan` produces the right `agents` tuple per workflow; `MasterAgent.execute` is now a 5-line delegate to the runner. The user message write and orchestration table writes stay in Master.

**Tasks:**

1. Create `config/workflows.yaml` (minimal Phase 9 form):

   ```yaml
   workflows:
     technical_deep:    { agents: ["persona:architect"], mode: sequential }
     quick_action:      { agents: ["persona:operator"],  mode: sequential }
     casual_reply:      { agents: ["persona:casual"],    mode: sequential }
     supportive_reply:  { agents: ["persona:casual"],    mode: sequential }
     research_brief:    { agents: ["research", "persona:architect"], mode: sequential }
     coding_session:    { agents: ["persona:architect"], mode: sequential }
     debate_then_synthesize: { agents: ["persona:architect"], mode: sequential }
     speculative_brief: { agents: ["persona:operator"],  mode: sequential }
   default_workflow: casual_reply
   ```

   Phase 11/12 will add `coding`, `execution`, `evaluator`, `critic`, and the real non-sequential modes; Phase 9 only needs `research` plus the persona wrappers.

2. `src/ubongo/router.py` extension. Today the router has a private `_WORKFLOW_TO_PERSONA` map (lines 21–30). Replace with a `workflows.yaml` reader that returns both `persona` and `agents` per workflow. Keep `route(classification) -> str` returning the persona for back-compat with hysteresis, and add `route_workflow(classification) -> str` returning the workflow **name**. New helper `workflow_agents(name) -> tuple[str, ...]`.

3. `MasterAgent.plan` rewires:

   ```python
   suggested_workflow_name = router.route_workflow(classification) if ctx.auto_mode else None
   chosen_persona = ctx.persona
   if ctx.auto_mode:
       suggested_persona = router.route(classification)
       chosen_persona = router.apply_hysteresis(ctx.persona, suggested_persona, classification.confidence)
   # Pick the workflow:
   #   - auto mode: use suggested_workflow_name if hysteresis kept the persona swap;
   #     else fall back to the persona-default workflow.
   #   - manual mode: use the persona-default workflow (today's behavior).
   wf_name = _resolve_workflow_name(chosen_persona, suggested_workflow_name, ctx.auto_mode)
   agents = router.workflow_agents(wf_name)
   workflow = Workflow(persona=chosen_persona, model=persona_obj.model,
                       skill_name=skill_name, execution_mode="sequential",
                       agents=tuple(agents))
   ```

   The persona default mapping (architect → technical_deep, etc.) is kept simple inside `_resolve_workflow_name`.

4. `MasterAgent.execute`:

   ```python
   def execute(self, workflow, ctx, message) -> WorkflowResult:
       events.dispatch("before_execute", {"workflow": asdict(workflow),
                                          "conversation_id": ctx.conversation_id})
       result = self._runner.execute(workflow, ctx, message,
                                     workflow_run_id=ctx.metadata.get("workflow_run_id"))
       events.dispatch("after_execute", {"workflow_result": asdict(result)})
       return result
   ```

   The `_runner` is `WorkflowRunner(default_registry())`, set in `MasterAgent.__init__`. The previous inline `complete(...)` + LLM-error handling moves into `PersonaAgent.run` (already covered by 9d).

5. **`workflow_run_id` threading.** Today (Phase 8), `master.handle` writes the `workflow_runs` row **after** `execute()` returns. For 9d the runner needs `workflow_run_id` to populate `agent_runs`. Fix: split the write — INSERT the `workflow_runs` row **before** `execute()` with `outcome='in_progress'` and `ended_at=NULL`, then UPDATE it after with the final outcome and `ended_at`. Schema check constraint says `outcome IN ('success', 'failure', 'repaired')`; we extend it to include `'in_progress'`. Migration: bump `schema.sql`, add a `_migrate_outcome_check` pass in `bootstrap()` that only runs if the constraint doesn't include `'in_progress'`. Alternative: defer the agent_runs write until after the workflow finishes (buffer in memory, flush at end). **Open question 3.**

6. **Memory Agent call site.** After the runner returns `WorkflowResult`, `MasterAgent.handle` calls `memory_agent.commit_assistant_turn(...)` instead of `store.append_message(role='assistant', ...)`. The Memory Agent emits its own `agent_runs` row (treated like any other agent in the workflow — see 9d step 4). The user message append (line 229 today) stays in Master.

7. Update `tests/test_master.py` and `tests/test_repl_summary.py` to reflect the new control flow (the LLM call is no longer in master.py — it's in PersonaAgent — but `master.handle` returns the same Response shape).

**Files modified:** `src/ubongo/master.py`, `src/ubongo/router.py`, `src/ubongo/memory/schema.sql` (+`'in_progress'` to outcome CHECK), `src/ubongo/memory/store.py` (`+update_workflow_run_outcome`, migration shim), `tests/test_master.py`, `tests/test_repl_summary.py`.
**Files added:** `config/workflows.yaml`.

### 9f — `/agents` REPL command

**Purpose:** Operator-visible list of registered workers.

**Tasks:**

1. In `repl.py`, add `_render_agents_table()`:

   ```python
   def _render_agents_table() -> str:
       reg = runner.default_registry()
       if not reg:
           return "No agents registered."
       lines = ["Registered agents:"]
       for name, agent in reg.items():
           model = agent.default_model or "—"
           lines.append(f"  {name:>22}  {agent.role:<48}  {model}")
       return "\n".join(lines)
   ```

2. Wire `/agents` into the slash dispatch (no arg). Update `_HELP_COMMANDS` to include it.

3. Update the Phase-1 smoke scenario 1.7 expected help line to include `/agents`.

4. Tests in `tests/test_repl_agents.py` (~4 tests):
   - `/agents` renders header + one line per registered agent.
   - Each persona wrapper appears with its model.
   - Research and Memory appear with the right `role`.
   - Empty registry → `No agents registered.`

**Files modified:** `src/ubongo/repl.py`, `tests/test_repl_agents.py` (new).

### 9g — STATUS + smoke playbook Phase 9 section

**Tasks:**

1. Append Phase 9 section to `tests/manual/smoke_test.md` with the 5 scenarios in the spec's testing plan, expanded with concrete commands and DB queries (mirroring Phase 8 section format).
2. Update `STATUS.md`: Phase 9 row → Complete; "Overall" paragraph; LOC bump.
3. Update smoke playbook scenario 1.7: help line now includes `/agents`.

**Files modified:** `tests/manual/smoke_test.md`, `STATUS.md`.

## Final file tree after Phase 9

```text
src/ubongo/
  agents/
    __init__.py
    base.py                              (new — Agent protocol, AgentInput, AgentResult)
    research.py                          (new — ResearchAgent)
    memory.py                            (new — MemoryAgent; writer-token machinery)
    personas.py                          (modified — +PersonaAgent wrapper class)
  memory/
    __init__.py                          (modified — registers MemoryAgent-mediated after_send)
    schema.sql                           (modified — +'in_progress' to workflow_runs CHECK)
    store.py                             (modified — +append_agent_run, +update_workflow_run_outcome,
                                                     +last_n_messages_global, migration shim)
    vault.py                             (modified — removes the direct events.register call)
  runner.py                              (new — sequential WorkflowRunner; default_registry)
  master.py                              (modified — plan() builds agents tuple per workflow;
                                                     execute() delegates to runner;
                                                     workflow_runs split into INSERT-then-UPDATE)
  router.py                              (modified — workflows.yaml reader, route_workflow,
                                                     workflow_agents helpers)
  context.py                             (modified — +agent_role param to build_system_prompt)
  repl.py                                (modified — /agents command; help line)
config/
  workflows.yaml                         (new — minimal workflow → agents/mode map)
  settings.yaml                          (modified — +models.research, +agents.research.max_tokens)
tests/
  test_agents_base.py                    (new — protocol + dataclass tests, ~4)
  test_agents_research.py                (new — retrieval + synthesis tests, ~6)
  test_agents_memory.py                  (new — single-writer + commit tests, ~5)
  test_runner.py                         (new — sequential dispatch + agent_runs tests, ~7)
  test_repl_agents.py                    (new — /agents rendering tests, ~4)
  test_master.py                         (modified — plan picks workflow.agents per intent;
                                                     execute delegates to runner)
  test_repl_summary.py                   (modified — LLM call site moved to PersonaAgent)
  test_memory_store.py                   (modified — +append_agent_run roundtrip,
                                                     +update_workflow_run_outcome,
                                                     +last_n_messages_global)
Plans/
  phase-9-research-memory.md             (new — this file)
STATUS.md                                (modified)
tests/manual/smoke_test.md               (modified — Phase 9 section + 1.7 help-line tweak)
```

Untouched: `classifier.py`, `delivery/queue.py`, `memory/compaction.py`, `skills.py`, `llm.py`, `governance/decision.py` (still always-`auto`), `oneshot.py`, `events.py`, `config/personas/*`, `config/skills/*`, `config/UBONGO.md`, `config/routing.yaml` (rules still drive workflow selection; only the lookup layer in `router.py` reads `workflows.yaml` instead of the hardcoded map).

## Testing plan

Manual smoke (appended as § Phase 9 in `tests/manual/smoke_test.md`):

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 9.1 | Behavior parity for non-research | Same prompts as Phase 8 baseline (architect technical question + casual line) | Identical-shape responses; queue still populated; vault note still appended; `workflow.agents == ('persona:architect',)` / `('persona:casual',)` in the workflow_runs row. |
| 9.2 | Research dispatched | `rm -f data/ubongo.db`; seed a few turns about caching; then `ubongo send "research what we discussed about caching" --persona architect` | Response cites prior turns (e.g., `[conv:1:msg:3]`); `agent_runs` has rows for `research`, `persona:architect`, `memory`; `workflow_runs.workflow` JSON shows `agents: ["research", "persona:architect"]`. |
| 9.3 | `/agents` | `/agents` | Header `Registered agents:`; lines for `research`, `memory`, `persona:architect`, `persona:operator`, `persona:casual` with role + model. |
| 9.4 | `agent_runs` populated | After 9.2: `sqlite3 data/ubongo.db "SELECT agent, outcome, tokens_in, tokens_out FROM agent_runs ORDER BY id DESC LIMIT 3"` | Three rows; outcomes `success`; FK to the latest `workflow_runs.id`. |
| 9.5 | Memory single-writer (strict test mode) | `uv run pytest tests/test_agents_memory.py::test_strict_mode_blocks_non_memory_writer` | Passes: a synthetic non-Memory caller into `store.append_message(role='assistant')` raises under the fixture; MemoryAgent path doesn't. |
| 9.6 | Casual still works (regression) | `/casual`; "long day" | Single-agent persona; casual voice; `agent_runs` has only the `persona:casual` + `memory` rows. |
| 9.7 | Research LLM failure | `OPENROUTER_API_KEY=sk-or-v1-bogus ubongo send "research caching" --persona architect 2>/tmp/p9.err` | Response is the persona answering without findings (no crash); `agent_runs` shows `research` outcome=`failure`, `persona:architect` outcome=`success`; `workflow_runs.outcome=success`; `agent_failed` dispatched. |
| 9.8 | `master_decision` still emitted | `grep master_decision /tmp/p9.err` | One JSON line per turn with the same fields as Phase 8 + workflow's new agent count visible via the workflow JSON. |
| 9.9 | Pytest passes | `uv run pytest tests/` | All green (existing ~165 + ~26 new ≈ 191). |

## Out of scope for Phase 9 (do NOT build)

- Evaluator Agent / Critic Agent / Persona Agents formalized as separate classes per persona — Phase 10. Phase 9 has a single `PersonaAgent(persona_name)` wrapper class instantiated three times.
- Coding / Execution / Repair agents — Phase 11.
- Parallel / competitive / collaborative / debate / speculative modes — Phase 12. Runner raises `NotImplementedError` for anything other than `sequential`.
- Real `composer.py`. Phase 10.
- `governance.yaml` parsing, real risk thresholds, real confidence gating — Phases 14–15.
- `agent_role` stanza content beyond a one-liner — research agent gets its first role frame; Phase 10 will expand for evaluator/critic.
- Embeddings, sqlite-vec, semantic recall — Phase 20.
- Vault graph queries — Phase 20.
- `/trace <n>` command — Phase 10.
- `/improvements` / GP loop — Phases 16+.
- Hard runtime enforcement of single-writer in production paths (assertions inside store.py / vault.py). Phase 11 revisits once Coding/Execution land.
- Migrating the user-message append into Memory Agent. Phase 11 (the spec says "Memory Agent is the single writer to durable memory"; user messages are inputs the Master ingests, not outputs the workflow produces — deferring this avoids reshuffling the conversation_id resolution dance unnecessarily in Phase 9).
- Migrating `session.upsert` into Memory Agent. Session is orchestration state, not durable memory in the same sense; revisit if it becomes a problem.
- Multi-turn / cross-conversation embeddings retrieval. Phase 9 retrieval is keyword overlap + recency, intentionally dumb.

## Open questions to confirm before I start

1. **Agent protocol stays sync in Phase 9 (recommended).** Spec line 115 shows `async def run`. Going async now would force `asyncio.run()` somewhere up the chain (master.handle or repl.run) and add a layer that pays off only in Phase 12. Lean sync; Phase 12 retrofits when parallel/competitive land. OK?
2. **Memory single-writer enforcement is soft in Phase 9 (recommended).** `_writer_token` ContextVar + helper exists, but the production store/vault functions do not assert it; only a pytest fixture-driven strict mode does. Avoids polluting production code until a real second writer appears (Phase 11 Coding/Execution). Spec scenario 4 ("test mode → raises") is met. Alternative: assertions live in production code now. I lean soft; the abstraction is cheaper to harden than to soften. OK?
3. **`workflow_runs` row written before `execute()` with `outcome='in_progress'`, updated after (recommended).** Needed so the runner can FK-link `agent_runs` rows by `workflow_run_id`. Requires extending the schema CHECK constraint from `('success', 'failure', 'repaired')` to `('in_progress', 'success', 'failure', 'repaired')` and a one-time migration in `bootstrap()`. Alternative: runner buffers agent_runs in memory and flushes after master writes the row. I lean INSERT-then-UPDATE — observability of in-progress workflows matters for Phase 13 Repair, and the migration is a 3-line SQL. OK?
4. **`config/workflows.yaml` introduced now (recommended).** Spec lists it under "Files touched" for Phase 9. Minimal content: workflow name → `{ agents, mode }`. Phase 12 expands the mode field beyond `sequential`. Alternative: keep the workflow table hardcoded in `master.plan` until Phase 12. I lean introduce now — `route_workflow()` and `workflow_agents()` cleanly cap a multi-phase abstraction. OK?
5. **Research retrieves from conversation memory by keyword overlap + recency, and from vault by `grep` over latest 30 daily-note files (recommended).** Intentionally dumb. Phase 20 swaps in `sqlite-vec` and the graph. Alternative: skip vault retrieval in Phase 9, conversation-only. I lean include vault — it's already on disk and a grep takes ~ms. OK?
6. **PersonaAgent is one wrapper class in `agents/personas.py` parameterized by name (recommended)**. Spec Phase 10 will introduce `ArchitectPersona` / `OperatorPersona` / `CasualPersona` as separate classes. Phase 9 just needs the protocol-conformant wrapper; one class with `__init__(self, persona_name)` is enough. OK?
7. **`agent_runs` rows recorded for every agent in the workflow, including `memory` (recommended).** Makes the orchestration trace symmetric and gives Phase 10's `/trace` a complete picture. Alternative: skip the `memory` row to avoid noise. I lean include — the row is cheap and the symmetric trace is worth more than the saved INSERT. OK?
8. **`_LLM_FAILURE_MESSAGE` moves from `master.py` to `runner.py`** (since the LLM call is no longer in master). REPL imports stay unchanged because nothing imports the constant by name. OK?

If you don't push back on any, I'll go with the defaults above.

## Definition of done for Phase 9

- Seven commits on `phase-9-research-memory` (9a, 9b, 9c, 9d, 9e, 9f, 9g).
- Smoke scenarios 9.1–9.8 pass; 9.9 pytest green.
- New tests: `test_agents_base.py` (~4), `test_agents_research.py` (~6), `test_agents_memory.py` (~5), `test_runner.py` (~7), `test_repl_agents.py` (~4). Existing tests still pass (with `test_master.py` and `test_repl_summary.py` updated for the runner delegation).
- `tests/manual/smoke_test.md` Phase 9 section appended; scenario 1.7 help line updated to include `/agents`.
- `STATUS.md` Phase 9 row → Complete; "Overall" paragraph refreshed; LOC count updated.
- Branch handed to you for merge. **Don't merge.**

---

(Verified: `origin/main` matches local `main` at `c26cec9`. Phase 8 fully merged. Branch `phase-9-research-memory` does not yet exist.)
