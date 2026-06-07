"""Shared model-call envelope for LLM Worker Agents (Phase 05).

Every LLM agent's `run()` wrapped the same envelope around one `llm.complete()`
call: a monotonic timer, `override_model` / `max_tokens_override` resolution off
`input.metadata`, the `LLMError -> AgentResult(ok=False)` mapping, a structured
log line, and the success-result assembly. ~8 near-identical copies. This module
owns that envelope once.

Prompt assembly, the repair-hint append, and result interpretation stay in each
agent's `run()` (see CONTEXT.md "Model call"); only the mechanical envelope lives
here. The shared `llm.complete()` seam (single retry, token/latency accounting,
`before_llm`/`after_llm` events) is unchanged.

`complete_fn` is passed in rather than imported here on purpose: the test suite
patches `complete` at each agent module (`patch("ubongo.agents.coding.complete",
...)`). Each agent keeps its own `from ubongo.llm import complete` and hands it in,
so those patches stay valid and the envelope is trivially unit-testable with a
fake.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Callable

from ubongo.agents.base import AgentResult
from ubongo.llm import CompletionResult, LLMError

if TYPE_CHECKING:
    from ubongo.agents.base import AgentInput

CompleteFn = Callable[..., CompletionResult]
OnSuccess = Callable[[CompletionResult], AgentResult]


def resolve_model(input: "AgentInput", default_model: str) -> str:
    """The override model from metadata, else the agent default."""
    return input.metadata.get("override_model") or default_model


def resolve_max_tokens(input: "AgentInput", default_max_tokens: int) -> int:
    """The override token cap from metadata, else the agent default."""
    return input.metadata.get("max_tokens_override") or default_max_tokens


def call_model_or_none(
    *,
    logger: logging.Logger,
    error_event: str,
    system_prompt: str,
    messages: list[dict],
    model: str,
    max_tokens: int,
    complete_fn: CompleteFn,
) -> CompletionResult | None:
    """Call the model, returning None on LLMError after logging `error_event`.

    For callers whose contract is `... | None` rather than AgentResult
    (`evaluator.rank` / `evaluator.agree`).
    """
    try:
        return complete_fn(
            system_prompt=system_prompt,
            messages=messages,
            model=model,
            max_tokens=max_tokens,
        )
    except LLMError as exc:
        logger.warning(
            error_event,
            extra={"model": model, "cause": str(exc.cause) if exc.cause else None},
        )
        return None


def run_agent_llm(
    *,
    agent_name: str,
    logger: logging.Logger,
    input: "AgentInput",
    system_prompt: str,
    messages: list[dict],
    default_model: str,
    default_max_tokens: int,
    complete_fn: CompleteFn,
    error_text: str = "",
    result_metadata: dict | None = None,
    log_extra: dict | None = None,
    success_log_extra: dict | None = None,
    on_success: OnSuccess | None = None,
) -> AgentResult:
    """Run one LLM call with the shared agent envelope.

    Resolves `override_model` / `max_tokens_override` off `input.metadata`, times
    the call, and maps `LLMError` to `AgentResult(ok=False,
    error="<agent_name>_llm_error")`. On success, either runs
    `on_success(completion)` (custom result, e.g. the Evaluator's JSON parse) or
    builds the standard text-passthrough `AgentResult` and logs `"<agent_name>_run"`.

    `log_extra` is merged into both the success and error log lines.
    `result_metadata` is set on both the standard success result and the error
    result. `success_log_extra` is merged into the success log only.
    """
    t0 = time.monotonic()
    model = resolve_model(input, default_model)
    max_tokens = resolve_max_tokens(input, default_max_tokens)
    extra = dict(log_extra or {})

    try:
        completion = complete_fn(
            system_prompt=system_prompt,
            messages=messages,
            model=model,
            max_tokens=max_tokens,
        )
    except LLMError as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        logger.warning(
            f"{agent_name}_llm_error",
            extra={"model": model, "cause": str(exc.cause) if exc.cause else None, **extra},
        )
        return AgentResult(
            text=error_text,
            ok=False,
            model=model,
            tokens_in=0,
            tokens_out=0,
            latency_ms=elapsed,
            error=f"{agent_name}_llm_error",
            metadata=dict(result_metadata or {}),
        )

    if on_success is not None:
        return on_success(completion)

    logger.info(
        f"{agent_name}_run",
        extra={
            "model": completion.model,
            "tokens_in": completion.tokens_in,
            "tokens_out": completion.tokens_out,
            "latency_ms": completion.latency_ms,
            "attempts": completion.attempts,
            **extra,
            **dict(success_log_extra or {}),
        },
    )
    return AgentResult(
        text=completion.text,
        ok=True,
        model=completion.model,
        tokens_in=completion.tokens_in,
        tokens_out=completion.tokens_out,
        latency_ms=completion.latency_ms,
        metadata=dict(result_metadata or {}),
    )
