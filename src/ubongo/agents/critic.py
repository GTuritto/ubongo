"""Critic Agent: contrarian frame against the prevailing answer.

Phase 10 uses the Critic in one path: Master invokes a second runner pass
(critic, persona) when Evaluator confidence falls in the borderline band
[0.2, 0.6). The Critic reads the candidate response from prior_findings,
optionally borrows the Evaluator's flagged issues if it sees them, and
produces up to ~5 bullets of pointed disagreement. The follow-up persona
pass then re-answers with the critique threaded into its context.

Phase 12 (debate mode) will use this same class in N-round arguments.

composer=False: critique text never becomes the WorkflowResult.text.
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

logger = logging.getLogger("ubongo.agents.critic")

_DEFAULT_MAX_TOKENS = 400
_EVAL_TEXT_PREFIX = "Confidence:"


def _extract_evaluator_issues(prior_findings: tuple[str, ...]) -> str | None:
    """If a prior finding looks like Evaluator output (starts with 'Confidence:'),
    return its body so the Critic's prompt can reference it. Cheap heuristic;
    false positives just give the Critic a bit more context."""
    for finding in reversed(prior_findings[:-1]):  # skip the candidate itself
        if finding.startswith(_EVAL_TEXT_PREFIX):
            return finding
    return None


_CRITIC_INSTRUCTION = (
    "You are the Critic Agent. Your job is to argue against the candidate "
    "response below. Find the weakest claim. Name one assumption that is "
    "load-bearing but unsupported. If the candidate is correct, say so in "
    "one line and stop; do not invent disagreement.\n\n"
    "Output: max 5 short bullets. No preamble."
)


class CriticAgent:
    name = "critic"
    role = "contrarian challenger: argue against the prevailing answer"
    composer = False

    def __init__(self) -> None:
        cfg = load_config()
        models = cfg.get("models", {})
        self.default_model = models.get("critic") or models.get("default", "")
        self.max_tokens = int(
            cfg.get("agents", {}).get("critic", {}).get("max_tokens", _DEFAULT_MAX_TOKENS)
        )

    def run(self, input: AgentInput, context: "Context") -> AgentResult:
        t0 = time.monotonic()
        candidate = input.prior_findings[-1] if input.prior_findings else ""
        if not candidate.strip():
            return AgentResult(
                text="",
                ok=False,
                model=self.default_model,
                tokens_in=0,
                tokens_out=0,
                latency_ms=int((time.monotonic() - t0) * 1000),
                error="critic_no_candidate",
            )

        sections = [
            build_system_prompt("operator", agent_role=self.role),
            _CRITIC_INSTRUCTION,
            "## User question\n\n" + input.message,
            "## Candidate response\n\n" + candidate,
        ]
        eval_findings = _extract_evaluator_issues(input.prior_findings)
        if eval_findings is not None:
            sections.append("## Evaluator flagged issues\n\n" + eval_findings)
        # Phase 13b: Repair may pass a prompt-hint addendum on a same-model retry.
        prompt_hint = input.metadata.get("repair_prompt_hint")
        if prompt_hint:
            sections.append("## Repair guidance\n\n" + prompt_hint)
        system_prompt = "\n\n".join(sections)

        model = input.metadata.get("override_model") or self.default_model
        max_tokens = input.metadata.get("max_tokens_override") or self.max_tokens
        try:
            completion = complete(
                system_prompt=system_prompt,
                messages=[{"role": "user", "content": "Argue against the candidate response."}],
                model=model,
                max_tokens=max_tokens,
            )
        except LLMError as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            logger.warning(
                "critic_llm_error",
                extra={"model": model, "cause": str(exc.cause) if exc.cause else None},
            )
            return AgentResult(
                text="",
                ok=False,
                model=model,
                tokens_in=0,
                tokens_out=0,
                latency_ms=elapsed,
                error="critic_llm_error",
            )

        logger.info(
            "critic_run",
            extra={
                "model": completion.model,
                "tokens_in": completion.tokens_in,
                "tokens_out": completion.tokens_out,
                "latency_ms": completion.latency_ms,
                "saw_evaluator_findings": eval_findings is not None,
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
