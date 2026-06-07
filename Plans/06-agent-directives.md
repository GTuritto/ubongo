# Plan — Candidate 06: Type the agent metadata seam (AgentDirectives)

Lifted from `Plans/05-09-architecture-deepening-roadmap.md` (Phase 06), refined
after reading the code. Branch `improve/06-agent-directives`; draft PR base `main`.
Pairs with the Phase 05 envelope (now merged).

## Problem

`AgentInput.metadata` is an untyped `dict`. The real interface is a set of string
keys the runner writes and the agents read by convention: `override_model`,
`max_tokens_override`, `repair_prompt_hint`, `debate_role`, `skill`,
`exec_command`. A typo returns `None` and the behaviour silently does not happen;
no test fails. The contract lives only in matching string literals across seven
files.

## Key discovery — `metadata` is dual-use

`AgentInput.metadata` carries two unrelated concerns:

1. **Directives** (orchestrator → agent control signals): the six keys above,
   read by the five LLM agents (via `llm_run`) and the Execution agent.
2. **Payload** (Master → Memory agent): `conversation_id`, `response_text`,
   `persona`, `model`, `tokens_in`, `tokens_out` — the assistant-turn record the
   Memory agent commits (`agents/memory.py:62-86`). This is an open, variable
   data record, not a fixed control surface.

Candidate 02's target is the **directives** seam. The memory payload is genuinely
dict-shaped and should stay a dict.

## Solution

- Add a frozen `AgentDirectives` dataclass (`agents/base.py`) with the six
  directive fields, all `None` by default.
- Add `AgentInput.directives: AgentDirectives` (default-constructed).
- **Keep `AgentInput.metadata: dict`** for the Memory agent payload (and any
  open data). The Memory agent is unchanged.
- Drop the `persona` directive entirely: the runner writes `metadata["persona"]`
  for every agent but **nothing reads it** (verified). Its only live use is the
  Memory payload, which keeps it in `metadata`.

```python
@dataclass(frozen=True)
class AgentDirectives:
    override_model: str | None = None
    max_tokens_override: int | None = None
    repair_prompt_hint: str | None = None
    debate_role: str | None = None
    skill: str | None = None
    exec_command: str | None = None
```

## Sub-phases

- **06.1** Define `AgentDirectives`; add `directives` to `AgentInput`. Unit test:
  unknown kwarg raises `TypeError` at construction (the anti-silent-drop guarantee).
- **06.2** `llm_run.resolve_model` / `resolve_max_tokens` read
  `input.directives.override_model` / `.max_tokens_override`.
- **06.3** Migrate agent reads to attributes: `repair_prompt_hint` (coding,
  critic, evaluator, research, personas), `skill` + `debate_role` (personas),
  `exec_command` (execution). Memory agent untouched.
- **06.4** `runner._dispatch_agent_async` builds `AgentDirectives` (skill from
  workflow, override_model, and explicit `repair_prompt_hint` /
  `max_tokens_override` / `debate_role` params replacing the `extra_metadata`
  dict). Update its two non-default callers (repair, debate).
- **06.5** Update tests that construct `AgentInput(metadata={<directive keys>})`
  to `AgentInput(directives=AgentDirectives(...))`. `test_agents_memory` and the
  Master memory-commit path keep `metadata=`.

## Behavior to preserve (guarded by tests)

- Every directive keeps its meaning and effect (model override, token cap, repair
  hint, debate role, skill, exec command).
- `complete` still called with keyword args (envelope unchanged in shape).
- Memory commit payload path (`conversation_id`/`response_text`/… via `metadata`).
- Error strings, composer rules, per-agent loggers (all from Phase 05).

## Risks / ADR check

- No ADR conflict. Note in PR: this is the typed restatement of the existing
  directive convention; the Memory payload stays a dict by design.
- Blast radius is mostly tests (construction sites). Net behavior identical.

## Tests

Full `pytest` green; new `AgentDirectives` construction-guard test; targeted
`test_agents_*`, `test_runner`, `test_master`, `test_repl_*`; Phases 0–21 smoke
(agent-execution paths, as in Phase 05).

## Out of scope

The Memory agent payload typing (could become its own `CommitPayload` type later;
not candidate 02). Phases 07-09.

## Done when

`AgentInput` carries `AgentDirectives`; no agent reads a directive by string key;
the Memory payload stays an explicit dict; full pytest green; smoke passes.
