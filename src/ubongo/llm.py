from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import litellm

from ubongo import events

logger = logging.getLogger("ubongo.llm")

litellm.suppress_debug_info = True
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

_RETRY_BACKOFF_SECONDS = 0.5


class LLMError(Exception):
    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


@dataclass(frozen=True)
class CompletionResult:
    text: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    attempts: int


def complete(
    system_prompt: str,
    messages: list[dict],
    model: str,
    max_tokens: int,
    temperature: float | None = None,
) -> CompletionResult:
    full_messages = [{"role": "system", "content": system_prompt}, *messages]

    events.dispatch(
        "before_llm",
        {"model": model, "max_tokens": max_tokens, "messages_count": len(full_messages)},
    )

    # Only forward temperature when a caller asks for one (the classifier pins
    # it to 0 for stable routing); otherwise leave the provider/model default.
    extra: dict = {} if temperature is None else {"temperature": temperature}

    last_exc: Exception | None = None
    start = time.perf_counter()

    for attempt in (1, 2):
        try:
            response = litellm.completion(
                model=model,
                messages=full_messages,
                max_tokens=max_tokens,
                **extra,
            )
            latency_ms = int((time.perf_counter() - start) * 1000)
            text = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
            tokens_in = getattr(usage, "prompt_tokens", 0) if usage else 0
            tokens_out = getattr(usage, "completion_tokens", 0) if usage else 0

            result = CompletionResult(
                text=text,
                model=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=latency_ms,
                attempts=attempt,
            )
            events.dispatch(
                "after_llm",
                {
                    "model": model,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "latency_ms": latency_ms,
                    "attempts": attempt,
                },
            )
            return result
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "llm_attempt_failed",
                extra={"model": model, "attempt": attempt, "error": str(exc)},
            )
            if attempt == 1:
                time.sleep(_RETRY_BACKOFF_SECONDS)

    raise LLMError(f"LLM call to {model} failed after 2 attempts", cause=last_exc)
