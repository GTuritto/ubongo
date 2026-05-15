"""Repair Agent: classifies failures + plans recovery for failed agent runs.

Phase 11 shipped a flat single-retry-with-model-fallback. Phase 13a introduces
the failure taxonomy that drives strategy selection: every agent error code
maps to a `FailureKind`; the strategy chooser in Phase 13b reads the kind,
not the raw error string.

Phase 13a is a pure refactor of the classifier — `plan_retry` keeps its
Phase-11 signature and behavior for the runner's current call site, but the
internal logic now goes through `_classify_failure`. Phases 13b–13g build
`plan_recovery`, peer replacement, repair_runs persistence, and the
runner's multi-strategy loop on top of this seam.

The Repair Agent itself does not run as part of any workflow; it is consulted
synchronously by the WorkflowRunner when an agent reports ok=False.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING

from ubongo.agents.base import AgentInput, AgentResult
from ubongo.config import load_config

if TYPE_CHECKING:
    from ubongo.master import Context

logger = logging.getLogger("ubongo.agents.repair")


class FailureKind(str, Enum):
    """How an agent failure is categorized for strategy selection.

    - TIMEOUT — the underlying LLM call or subprocess ran past its deadline.
    - MODEL_ERROR — generic LLM transport failure (HTTP 5xx, auth, rate-limit
      after litellm's own retries). Today's `*_llm_error` codes land here.
    - PARSE_ERROR — the model returned text that failed our schema check
      (e.g., `evaluator_parse_error`). A stricter-prompt retry pays here.
    - CONTENT_REJECTION — the model refused or returned empty content. Rare
      v0.1; reserved for future use as agents emit explicit refusal codes.
    - PRECONDITION_MISSING — the agent's input contract wasn't met
      (`critic_no_candidate`, `memory_missing_input`, `execution_no_command`).
      Re-prompting the same agent is futile; only peer replacement / abort.
    - INFINITE_LOOP — placeholder for Phase 18+ when GP introduces dynamic
      routing that could cycle. v0.1 has no detector.
    - UNRECOVERABLE — by-design refusals (sandbox `execution_refused`),
      unknown error codes, or memory-write failures. ABORT only.
    """

    TIMEOUT = "timeout"
    MODEL_ERROR = "model_error"
    PARSE_ERROR = "parse_error"
    CONTENT_REJECTION = "content_rejection"
    PRECONDITION_MISSING = "precondition_missing"
    INFINITE_LOOP = "infinite_loop"
    UNRECOVERABLE = "unrecoverable"


# Map agent error codes (the strings each agent sets on AgentResult.error)
# to a failure kind. Codes not in the map fall through to UNRECOVERABLE per
# `_classify_failure`.
_ERROR_KIND: dict[str, FailureKind] = {
    # LLM transport errors (timeouts surface as model_error from litellm
    # today; Phase 14 may add a dedicated TimeoutError code).
    "persona_llm_error":    FailureKind.MODEL_ERROR,
    "research_llm_error":   FailureKind.MODEL_ERROR,
    "evaluator_llm_error":  FailureKind.MODEL_ERROR,
    "critic_llm_error":     FailureKind.MODEL_ERROR,
    "coding_llm_error":     FailureKind.MODEL_ERROR,
    # Parse errors — stricter-schema retry is the natural fix.
    "evaluator_parse_error":       FailureKind.PARSE_ERROR,
    "evaluator_rank_parse_error":  FailureKind.PARSE_ERROR,
    "evaluator_agree_parse_error": FailureKind.PARSE_ERROR,
    "classifier_parse_error":      FailureKind.PARSE_ERROR,
    # Precondition failures — input contract not met. Re-prompting the same
    # agent with the same empty input is futile; only peer replacement
    # (or abort) makes sense.
    "critic_no_candidate":  FailureKind.PRECONDITION_MISSING,
    "memory_missing_input": FailureKind.PRECONDITION_MISSING,
    "execution_no_command": FailureKind.PRECONDITION_MISSING,
    # By-design refusals: sandbox said no on purpose. Do not retry.
    "execution_refused": FailureKind.UNRECOVERABLE,
}


def _classify_failure(agent_name: str, error_code: str | None) -> FailureKind:
    """Map an agent failure to a FailureKind for strategy selection.

    Order of checks:
    1. Memory writes (except memory_missing_input) are never retried — DB
       rollback needs Phase 21 infrastructure, not Phase 13's strategies.
    2. Known error codes hit `_ERROR_KIND` and return their mapped kind.
    3. `error_code is None` means the agent raised before setting one (the
       runner caught the exception and built a fallback AgentResult); treat
       as a generic MODEL_ERROR worth one retry with a different model.
    4. Anything else is UNRECOVERABLE — we don't invent strategies for
       error strings we don't recognize.
    """
    if agent_name == "memory" and error_code != "memory_missing_input":
        return FailureKind.UNRECOVERABLE
    if error_code is None:
        return FailureKind.MODEL_ERROR
    if error_code in _ERROR_KIND:
        return _ERROR_KIND[error_code]
    return FailureKind.UNRECOVERABLE


# Phase 11's `_RETRYABLE_ERRORS` / `_NEVER_RETRY_AGENTS` flat sets retired:
# the taxonomy expresses both. The list below records what Phase 11 used to
# treat as retryable so plan_retry's behavior stays equivalent in 13a.
# Phase 13b replaces plan_retry with plan_recovery; this constant goes away
# at that point.
_PHASE_11_RETRYABLE_KINDS: frozenset[FailureKind] = frozenset({
    FailureKind.MODEL_ERROR,
})


class RepairAgent:
    name = "repair"
    role = "detects and recovers failed agent runs (Phase 13 multi-strategy)"
    composer = False
    default_model = ""

    def __init__(self) -> None:
        cfg = load_config()
        models = cfg.get("models", {})
        # Sensible defaults: every agent falls back to models.default,
        # except casual which stays on its cheap model.
        defaults = {
            "coding": models.get("default", ""),
            "architect": models.get("default", ""),
            "operator": models.get("default", ""),
            "casual": models.get("casual", models.get("default", "")),
            "research": models.get("default", ""),
            "evaluator": models.get("default", ""),
            "critic": models.get("default", ""),
        }
        overrides = cfg.get("agents", {}).get("repair", {}).get("fallback_models", {}) or {}
        self._fallback_models = {**defaults, **overrides}

    # The Agent.run hook is required by the protocol but is a no-op for
    # Repair v0.1: the runner calls plan_retry / plan_recovery directly.
    # This keeps Repair registry-discoverable without putting it into any
    # workflow.agents list.
    def run(self, input: AgentInput, context: "Context") -> AgentResult:
        return AgentResult(
            text="", ok=True, model=None,
            tokens_in=0, tokens_out=0, latency_ms=0,
            metadata={"note": "RepairAgent.run is a no-op; runner calls plan_recovery"},
        )

    def plan_retry(
        self,
        failed_agent_name: str,
        original_result: AgentResult,
        input: AgentInput,
    ) -> dict | None:
        """Phase 11 sequential-runner hook. Returns {"model": fallback} for
        one retry, or None to give up.

        Phase 13a rewrites the internals to go through `_classify_failure`,
        but the externally-observable behavior matches Phase 11:
          - MODEL_ERROR (the old `*_llm_error` set)        -> retry with fallback
          - everything else (incl. PARSE_ERROR for now)    -> None

        PARSE_ERROR returns None here even though its plan_recovery ladder
        will lead with `same_model_repair_prompt`: Phase 11's `plan_retry`
        contract is only "one model swap, or give up", which the new
        prompt-addendum strategy doesn't fit. Phase 13b's `plan_recovery`
        exposes the full ladder; 13g switches the runner over.
        """
        kind = _classify_failure(failed_agent_name, original_result.error)
        if kind not in _PHASE_11_RETRYABLE_KINDS:
            logger.info(
                "repair_no_retry",
                extra={
                    "agent": failed_agent_name,
                    "error": original_result.error,
                    "kind": kind.value,
                },
            )
            return None
        fallback = self._fallback_models.get(failed_agent_name)
        if not fallback:
            logger.info(
                "repair_no_fallback_model",
                extra={"agent": failed_agent_name, "kind": kind.value},
            )
            return None
        logger.info(
            "repair_retry_planned",
            extra={
                "agent": failed_agent_name,
                "fallback_model": fallback,
                "original_error": original_result.error,
                "kind": kind.value,
            },
        )
        return {"model": fallback}


default_repair_agent = RepairAgent()
