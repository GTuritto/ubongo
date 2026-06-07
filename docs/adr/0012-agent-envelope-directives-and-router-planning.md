# 0012 — Model-call envelope, typed agent directives, and router-owned workflow planning

Status: Accepted
Date: 2026-06-07 (post-v0.1 architecture-deepening candidates 05, 06, 08; PRs #26, #27, #28)

## Context

v0.1 shipped complete (Phases 0–21). A `/improve-codebase-architecture` review of
the merged `main` surfaced three shallow seams in the orchestration core — places
where the interface was nearly as complex as the implementation, or where a
contract lived only in convention:

1. **Worker-agent boilerplate.** Every LLM agent's `run()` re-implemented the same
   envelope around one `llm.complete()` call: a monotonic timer, `override_model`
   / `max_tokens_override` resolution, the `LLMError → AgentResult(ok=False,
   error="<name>_llm_error")` mapping, a `"<name>_run"` log line, and result
   assembly. ~8 near-identical copies (coding, critic, research, personas,
   evaluator.run, plus evaluator.rank/agree).
2. **Untyped directive seam.** `AgentInput.metadata` was a bare `dict`; the
   orchestrator→agent contract was ~6 string keys (`override_model`,
   `max_tokens_override`, `repair_prompt_hint`, `debate_role`, `skill`,
   `exec_command`) read by convention across the agents. A misspelled key was a
   silent no-op.
3. **Scattered workflow planning.** `master.plan` assembled a `Workflow` from 7+
   separate router calls and owned the name-resolution rules; the runner then
   re-validated mode/agents invariants at execute time.

These are refactors, not behavior changes, and they stay inside the existing ADRs
(0001 hand-rolled orchestration; 0003 the master pipeline + six modes).

## Decision

- **One model-call envelope** (`src/ubongo/agents/llm_run.py`). `run_agent_llm`
  owns the mechanical envelope and returns `AgentResult` (with an `on_success`
  hook for the Evaluator's JSON parse); `call_model_or_none` serves the `… | None`
  callers (`evaluator.rank` / `agree`). Each agent passes its own module-level
  `complete` as `complete_fn` so per-agent test patches stay valid. **Prompt
  assembly, the repair-hint append, and result interpretation stay in each
  agent's `run()`** — only the mechanical envelope moved. This reverses the
  earlier CONTEXT.md "Model call" wording ("no separate `call_model` layer"): the
  envelope is a mechanical seam, not an invocation/routing layer, and it completes
  the job the glossary already assigned to the shared `complete()` seam.

- **Typed agent directives** (`AgentDirectives`, `agents/base.py`). A frozen
  dataclass carried on `AgentInput.directives` replaces the untyped-dict directive
  keys; a misspelled directive now fails at construction. `AgentInput.metadata`
  stays a `dict` for the **Memory agent's commit payload** (`conversation_id`,
  `response_text`, …) — a genuinely open, variable record, not a fixed control
  surface. The write-only `persona` directive (never read) was dropped.

- **Router-owned workflow planning** (`router.plan_workflow` →
  `WorkflowPlan`). One deep call owns routing, hysteresis, the `/mode` override,
  name resolution, the agent list + evaluator append, and structural mode/agents
  validation at plan time. A router-owned `WorkflowPlan` (not `master.Workflow`)
  avoids an import cycle; `master.plan` maps it to `Workflow` by adding the persona
  model and resolved skill, keeping the split clean: **router = config, master =
  turn state + registries**. The runner keeps its raises as a registry-aware
  backstop (it also checks that named agents exist and that the evaluator has
  `rank()`, which the router cannot see).

## Consequences

- Locality and leverage: the agent envelope, the directive contract, and the
  workflow-planning rules each live in one place; a change is one edit, not N.
  `master.plan` shrank from ~50 lines to a delegate plus model/skill assembly.
- Two intentional, test-invisible deltas from the envelope: error logging
  standardized to `logger.warning` (coding/personas were `error`), and the
  standard success log always includes `attempts`.
- No ADR conflict; ADRs 0001/0003/0008 hold unchanged (pipeline shape,
  effective-config precedence, live swap). The only superseded text is the
  CONTEXT.md "Model call" glossary wording, updated in the same work.
- Verified: full pytest green (779) and the Phases 0–21 cumulative smoke pass with
  these changes on `main`.

References: `Plans/05-agent-llm-envelope.md`, `Plans/06-agent-directives.md`,
`Plans/08-router-plan-workflow.md`, `Plans/05-09-architecture-deepening-roadmap.md`;
`src/ubongo/agents/llm_run.py`, `src/ubongo/agents/base.py`, `src/ubongo/router.py`;
`CONTEXT.md` ("Model call").
