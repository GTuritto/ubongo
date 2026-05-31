# Ubongo

The domain language of Ubongo: a single-user, multi-agent AI mind that runs locally as a CLI. This glossary names the concepts the code is built around, so that issues, refactors, and tests use one vocabulary.

## Language

**Worker Agent**:
A disposable unit the Master Agent dispatches to do one job in a turn (Research, Coding, Critic, Evaluator, Execution, Persona, Memory, Repair). It satisfies the `Agent` interface: `name`, `role`, `default_model`, and `run()`.
_Avoid_: service, component, worker (bare).

**Invocation**:
One Worker Agent's call to the model behind the shared runtime. The runtime (`call_model` / `invoke_agent` in `llm_invocation.py`) owns the parts every LLM-calling agent shares: effective model and token-budget resolution, the repair-hint append, the `complete()` call, error handling, logging, and token/latency accounting. An agent contributes only an `Invocation` spec: how to build its prompt, which messages to send, an optional precondition, and an optional interpret step that shapes the semantic fields of the result.
_Avoid_: model call (bare), LLM step, request.

**Composer**:
The one Worker Agent in a workflow whose output becomes the user-facing response. Marked by a `composer = True` attribute; `WorkflowResult.text` is taken from the last composer to run. Validators (Evaluator, Critic) and helpers (Research, Execution) contribute findings but are not composers.
_Avoid_: responder, finalizer.

**Finding**:
What a non-composer Worker Agent returns for downstream agents to build on, threaded forward as `prior_findings`. A Finding is evidence or critique, never the durable record and never (by itself) the response.
_Avoid_: result (bare), output.

## Example dialogue

> **Dev:** When the Evaluator scores a turn, is that an Invocation?
> **Domain expert:** Yes. Its `run()` builds the judge prompt and hands back an `Invocation` spec; the runtime makes the model call and accounts the tokens. The Evaluator's interpret step turns the completion into a confidence plus issues.
> **Dev:** And `rank()` and `agree()`?
> **Domain expert:** Same model call underneath (they share `call_model`), but they are not the agent's `run()` and the Evaluator is never a Composer — it returns a Finding, not the response. The Persona agent is the Composer there.
