"""Coding Agent: code generation, refactoring, review.

Phase 11 ships the Coding Agent as a strong-coding-model LLM call with a
code-first system prompt. It is a composer (text becomes the response
unless a later composer agent runs after it). In coding_session the
agents list is ("coding", "architect"): Coding produces the function;
Architect wraps it in tradeoffs commentary; last-composer-wins makes the
Architect's text the user-facing response and Coding's text rides in
prior_findings for the Architect to quote verbatim.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from ubongo.agents.base import AgentInput, AgentResult
from ubongo.config import load_config
from ubongo.context import build_system_prompt
from ubongo.llm import LLMError, complete

if TYPE_CHECKING:
    from ubongo.master import Context

logger = logging.getLogger("ubongo.agents.coding")

_DEFAULT_MAX_TOKENS = 2048

_CODING_INSTRUCTION = (
    "You are the Coding Agent. Produce working code. When the user asks for code:\n"
    "- Write the function or module they asked for; do not write a plan instead.\n"
    "- Include type hints, a short docstring, and one usage example.\n"
    "- Name what you assumed when the spec was ambiguous.\n"
    "- If the request is too broad to write in one pass, ask for the one\n"
    "  concrete thing you need to know; do not write a half-implementation."
)


class CodingAgent:
    name = "coding"
    role = "code generation, refactoring, review"
    composer = True

    def __init__(self) -> None:
        cfg = load_config()
        models = cfg.get("models", {})
        self.default_model = models.get("coding") or models.get("default", "")
        self.max_tokens = int(
            cfg.get("agents", {}).get("coding", {}).get("max_tokens", _DEFAULT_MAX_TOKENS)
        )

    def run(self, input: AgentInput, context: "Context") -> AgentResult:
        t0 = time.monotonic()
        base = build_system_prompt("architect", agent_role=self.role)
        sections: list[str] = [base, _CODING_INSTRUCTION]
        if input.summary_text:
            sections.append(f"## Conversation summary so far\n\n{input.summary_text}")
        for i, finding in enumerate(input.prior_findings, start=1):
            sections.append(f"## Prior agent findings #{i}\n\n{finding}")
        system_prompt = "\n\n".join(sections)
        model = input.metadata.get("override_model") or self.default_model

        try:
            completion = complete(
                system_prompt=system_prompt,
                messages=list(input.history),
                model=model,
                max_tokens=self.max_tokens,
            )
        except LLMError as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            logger.error(
                "coding_llm_error",
                extra={"model": model, "cause": str(exc.cause) if exc.cause else None},
            )
            return AgentResult(
                text="",
                ok=False,
                model=model,
                tokens_in=0,
                tokens_out=0,
                latency_ms=elapsed,
                error="coding_llm_error",
            )

        logger.info(
            "coding_run",
            extra={
                "model": completion.model,
                "tokens_in": completion.tokens_in,
                "tokens_out": completion.tokens_out,
                "latency_ms": completion.latency_ms,
                "had_findings": bool(input.prior_findings),
            },
        )
        return AgentResult(
            text=completion.text,
            ok=True,
            model=completion.model,
            tokens_in=completion.tokens_in,
            tokens_out=completion.tokens_out,
            latency_ms=completion.latency_ms,
        )
