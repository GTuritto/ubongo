# Plan — Candidate 05: Collapse the worker-agent model-call envelope

Lifted from `Plans/05-09-architecture-deepening-roadmap.md` (Phase 05). Branch
`improve/05-agent-llm-envelope`; draft PR base `main`.

## Problem

Every LLM Worker Agent re-implements the same envelope around one
`llm.complete()` call: a monotonic timer, `override_model` /
`max_tokens_override` resolution off `input.metadata`, the
`try/except LLMError → AgentResult(ok=False, error="<name>_llm_error")` mapping,
a `logger.info("<name>_run", …)` line, and the success `AgentResult` assembly.
Confirmed identical in `agents/coding.py:53-109` and `agents/critic.py:67-138`,
repeated in `research.py`, `personas.py`, and `evaluator.py` (`run` plus the
`rank`/`agree` entry points). ~8 copies. Each `run()` is **shallow**: the
envelope is most of it; the only real difference is prompt assembly.

## Solution — one deep seam (`src/ubongo/agents/llm_run.py`)

Two functions own the envelope:

- `run_agent_llm(*, agent_name, logger, input, system_prompt, messages,
  default_model, default_max_tokens, complete_fn, error_text="",
  result_metadata=None, log_extra=None, success_log_extra=None,
  on_success=None) -> AgentResult` — times the call, resolves
  `override_model`/`max_tokens_override`, maps `LLMError` to the standard error
  `AgentResult`, and on success either runs `on_success(completion)` (custom
  result, e.g. the Evaluator's JSON parse) or builds the standard
  text-passthrough result and logs `"<agent_name>_run"`.
- `call_model_or_none(*, logger, error_event, system_prompt, messages, model,
  max_tokens, complete_fn) -> CompletionResult | None` — for the `… | None`
  callers (`evaluator.rank` / `agree`): call, log + return `None` on `LLMError`.

**Prompt assembly, the repair-hint append, and result interpretation stay in
each agent's `run()`** (consistent with CONTEXT.md "Model call"); only the
mechanical envelope moves. The shared `llm.complete()` seam (single retry,
token/latency accounting, `before_llm`/`after_llm` events) is unchanged.

### Why `complete_fn` is passed in (not imported by the envelope)

The existing tests patch `complete` at each agent module
(`patch("ubongo.agents.coding.complete", …)`, ~60 sites across `test_master`,
`test_agents_*`, `test_repl_*`). Each agent keeps its
`from ubongo.llm import complete` and passes `complete` as `complete_fn`, so the
patch target stays valid and **no test changes are needed**. The envelope calls
`complete_fn(system_prompt=…, messages=…, model=…, max_tokens=…)` with keyword
args (tests assert `m.call_args.kwargs["system_prompt"|"model"|"max_tokens"]`).

## Sub-phases

- **05.1** Add `agents/llm_run.py` + `tests/test_agents_llm_run.py` (success path,
  `LLMError` path, model/max-tokens resolution, `on_success` routing, keyword-arg
  call shape) against a fake `complete_fn`.
- **05.2** Migrate composers: `coding.py`, `personas.py`.
- **05.3** Migrate helpers/validators: `research.py`, `critic.py`.
- **05.4** Migrate `evaluator.py`: `run` (via `on_success` for the parse), then
  `rank`/`agree` (via `call_model_or_none`).
- **05.5** Leave `execution.py` out (no LLM call; `default_model=""`); note in PR.

## Behavior to preserve (guarded by tests)

- Per-agent error string `"<name>_llm_error"` (and `evaluator_no_candidate`,
  `evaluator_parse_error`, `critic_no_candidate` early returns stay in `run()`).
- `complete` called with keyword args; called once per `run()`.
- `result.metadata` for research (`retrieved_*`) and evaluator (`issues`, `raw`).
- Composer attributes and last-composer-wins.
- Per-agent `logger` identity (`ubongo.agents.<name>`) and event names
  (`<name>_run`, `<name>_llm_error`) — passed in via the `logger`/`agent_name`
  params.

## Known, intentional deltas (note in PR)

- Error logging standardizes to `logger.warning` (today `coding`/`personas` use
  `logger.error`; `critic`/`research`/`evaluator` already use `warning`). No test
  asserts level.
- The standard success log now always includes `attempts` (today only `personas`
  logs it). Strictly additive; no test asserts log extras.

## Risks / ADR / CONTEXT check

- **CONTEXT.md "Model call"** says there is "no separate invocation/`call_model`
  layer." This relocates only the mechanical envelope (prompt assembly stays in
  `run()`), completing the seam's stated job. Update that glossary entry in this
  phase to describe the envelope; get sign-off on the wording before marking the
  PR ready.
- No ADR governs agent internals; no ADR conflict.

## Tests

Full `pytest` green (esp. `test_agents_*`, `test_master`, `test_runner`,
`test_repl_*`), new `test_agents_llm_run.py`, and Phases 0–21 cumulative smoke.

## Out of scope

Typing the metadata seam (that is Phase 06 — `AgentDirectives`); this phase still
reads `input.metadata.get(...)` inside the envelope.

## Done when

All LLM agents route through `llm_run`; the envelope exists once; full pytest
green; smoke passes; CONTEXT.md "Model call" updated.
