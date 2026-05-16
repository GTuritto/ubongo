"""Repair Agent: classifies failures + plans multi-strategy recovery.

Phase 11 shipped a flat single-retry-with-model-fallback (the `plan_retry`
hook). Phase 13a added the failure taxonomy that drives strategy selection.
Phase 13b adds `plan_recovery`: walk an ordered strategy ladder per failure
kind, returning a `RecoveryPlan` the runner can execute one step at a time.

`plan_retry` is kept as a thin Phase-11-compatible shim; the runner's
sequential strategy uses `plan_recovery` directly (Phase 13b switches the
sequential mode over). Fan-out modes will gain a slimmer
`_maybe_replace_failed` helper in Phase 13c that calls plan_recovery with
attempts_so_far=() and acts only on REPLACE_WITH_PEER.

The Repair Agent itself does not run as part of any workflow; it is
consulted synchronously by the WorkflowRunner.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from ubongo.agents.base import AgentInput, AgentResult
from ubongo.config import load_config

if TYPE_CHECKING:
    from ubongo.master import Context

logger = logging.getLogger("ubongo.agents.repair")


class FailureKind(str, Enum):
    """How an agent failure is categorized for strategy selection.

    See _ERROR_KIND for the agent-error-code → kind mapping and the
    Plans/phase-13-repair.md "Failure taxonomy" section for the design rationale.
    """

    TIMEOUT = "timeout"
    MODEL_ERROR = "model_error"
    PARSE_ERROR = "parse_error"
    CONTENT_REJECTION = "content_rejection"
    PRECONDITION_MISSING = "precondition_missing"
    INFINITE_LOOP = "infinite_loop"
    UNRECOVERABLE = "unrecoverable"


class Strategy(str, Enum):
    """One step of the recovery ladder."""

    RETRY_SAME_MODEL_VARIANT_PROMPT = "retry_same_model_variant_prompt"
    RETRY_DIFFERENT_MODEL_SAME_PROMPT = "retry_different_model_same_prompt"
    RETRY_SMALLER_MODEL_SHORTER_PROMPT = "retry_smaller_model_shorter_prompt"
    REPLACE_WITH_PEER = "replace_with_peer"
    ABORT = "abort"


@dataclass(frozen=True)
class RecoveryPlan:
    """Concrete instructions for the runner to attempt one recovery step.

    The runner inspects `strategy` to decide how to execute:
      - RETRY_*_VARIANT_PROMPT  : re-dispatch with `prompt_hint` metadata
      - RETRY_DIFFERENT_MODEL_* : re-dispatch with `override_model`
      - RETRY_SMALLER_MODEL_*   : re-dispatch with `override_model` + `prompt_hint`
                                  + `max_tokens_cap`
      - REPLACE_WITH_PEER       : dispatch `peer_agent` from the registry
                                  in the failing agent's slot (Phase 13c)
      - ABORT                   : stop; runner returns the original failure
    """

    strategy: Strategy
    override_model: str | None = None
    prompt_hint: str | None = None
    max_tokens_cap: int | None = None
    peer_agent: str | None = None
    reason: str | None = None


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


# Per-kind strategy ladder. The runner walks each in order, skipping any
# strategy already attempted (tracked in attempts_so_far) and any strategy
# that can't be materialized (e.g., no peer configured). ABORT terminates
# the ladder.
_STRATEGY_LADDER: dict[FailureKind, tuple[Strategy, ...]] = {
    FailureKind.PARSE_ERROR: (
        Strategy.RETRY_SAME_MODEL_VARIANT_PROMPT,
        Strategy.RETRY_DIFFERENT_MODEL_SAME_PROMPT,
        Strategy.REPLACE_WITH_PEER,
        Strategy.ABORT,
    ),
    FailureKind.CONTENT_REJECTION: (
        Strategy.RETRY_SAME_MODEL_VARIANT_PROMPT,
        Strategy.REPLACE_WITH_PEER,
        Strategy.ABORT,
    ),
    FailureKind.MODEL_ERROR: (
        Strategy.RETRY_DIFFERENT_MODEL_SAME_PROMPT,
        Strategy.RETRY_SMALLER_MODEL_SHORTER_PROMPT,
        Strategy.REPLACE_WITH_PEER,
        Strategy.ABORT,
    ),
    FailureKind.TIMEOUT: (
        Strategy.RETRY_SMALLER_MODEL_SHORTER_PROMPT,
        Strategy.REPLACE_WITH_PEER,
        Strategy.ABORT,
    ),
    # Option A: input-contract failures skip variant-prompt retries because
    # re-prompting an agent with no candidate to critique is futile.
    FailureKind.PRECONDITION_MISSING: (
        Strategy.REPLACE_WITH_PEER,
        Strategy.ABORT,
    ),
    FailureKind.INFINITE_LOOP: (Strategy.ABORT,),
    FailureKind.UNRECOVERABLE: (Strategy.ABORT,),
}


# Per-kind prompt hint added to the agent's system prompt on a same-model retry.
_PROMPT_HINTS: dict[FailureKind, str] = {
    FailureKind.PARSE_ERROR: (
        "The previous attempt returned text that could not be parsed. "
        "Return ONLY a JSON object matching the schema described above. "
        "No prose, no markdown fences, no commentary."
    ),
    FailureKind.CONTENT_REJECTION: (
        "The previous attempt did not produce a usable response. Answer "
        "the user's question directly. If the question is genuinely "
        "unanswerable, say so in one sentence."
    ),
    # MODEL_ERROR, TIMEOUT, PRECONDITION_MISSING have no hint — same-model
    # variant-prompt isn't in their ladder.
}

# Prompt hint added when retrying with a smaller model — pushes the model
# to be brief so cost stays bounded.
_SMALLER_MODEL_HINT = "Be concise. Answer in under 200 tokens."
_SMALLER_MODEL_TOKEN_CAP = 200


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
# treat as retryable so plan_retry's behavior stays equivalent in 13a/13b.
# Phase 13g switches the runner over to plan_recovery proper and the
# plan_retry shim retires.
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
        repair_cfg = cfg.get("agents", {}).get("repair", {}) or {}
        # Cap total strategy attempts per agent failure. Configurable so
        # tests can dial it down (and Phase 17 can dial it up for evolution).
        self.max_attempts: int = int(repair_cfg.get("max_attempts", 3))
        # Fallback (different) model for MODEL_ERROR retries.
        fb_defaults = {
            "coding": models.get("default", ""),
            "architect": models.get("default", ""),
            "operator": models.get("default", ""),
            "casual": models.get("casual", models.get("default", "")),
            "research": models.get("default", ""),
            "evaluator": models.get("default", ""),
            "critic": models.get("default", ""),
        }
        fb_overrides = repair_cfg.get("fallback_models", {}) or {}
        self._fallback_models = {**fb_defaults, **fb_overrides}
        # Smaller model for SMALLER_MODEL retries. Defaults to models.casual
        # for every agent; specific overrides honored via settings.
        smaller_default = models.get("casual", models.get("default", ""))
        sm_defaults = {agent: smaller_default for agent in fb_defaults}
        sm_overrides = repair_cfg.get("smaller_models", {}) or {}
        self._smaller_models = {**sm_defaults, **sm_overrides}
        # Peer replacements. Defaults populated in Phase 13c; here we read
        # whatever settings.yaml provides. Empty / missing entries disable
        # the strategy for that agent (plan_recovery skips to next).
        self._peer_replacements: dict[str, str | None] = dict(
            repair_cfg.get("peer_replacements", {}) or {}
        )

    # The Agent.run hook is required by the protocol but is a no-op for
    # Repair v0.1: the runner calls plan_retry / plan_recovery directly.
    def run(self, input: AgentInput, context: "Context") -> AgentResult:
        return AgentResult(
            text="", ok=True, model=None,
            tokens_in=0, tokens_out=0, latency_ms=0,
            metadata={"note": "RepairAgent.run is a no-op; runner calls plan_recovery"},
        )

    # ---------- Phase 13b: multi-strategy recovery ----------

    def plan_recovery(
        self,
        *,
        failed_agent: str,
        original: AgentResult,
        attempts_so_far: tuple[Strategy, ...],
    ) -> RecoveryPlan:
        """Walk the strategy ladder for this failure kind, skipping
        strategies already in `attempts_so_far` and strategies that can't
        be materialized (e.g., no peer configured, no smaller model).

        Returns a RecoveryPlan. When the ladder is exhausted or
        max_attempts is reached, returns ABORT. The runner always receives
        a RecoveryPlan, never None — ABORT is the explicit "give up" signal.
        """
        kind = _classify_failure(failed_agent, original.error)
        ladder = _STRATEGY_LADDER.get(kind, (Strategy.ABORT,))

        if len(attempts_so_far) >= self.max_attempts:
            logger.info(
                "repair_max_attempts_reached",
                extra={
                    "agent": failed_agent,
                    "attempts": len(attempts_so_far),
                    "max": self.max_attempts,
                    "kind": kind.value,
                },
            )
            return RecoveryPlan(
                strategy=Strategy.ABORT,
                reason=f"max_attempts_reached:{self.max_attempts}",
            )

        for strategy in ladder:
            if strategy in attempts_so_far:
                continue
            plan = self._materialize_plan(strategy, failed_agent, kind)
            if plan is not None:
                logger.info(
                    "repair_plan",
                    extra={
                        "agent": failed_agent,
                        "kind": kind.value,
                        "strategy": plan.strategy.value,
                        "attempt_index": len(attempts_so_far),
                        "original_error": original.error,
                    },
                )
                return plan
            # Strategy not materializable (e.g., no peer); skip to next.
        return RecoveryPlan(strategy=Strategy.ABORT, reason="ladder_exhausted")

    def _materialize_plan(
        self,
        strategy: Strategy,
        failed_agent: str,
        kind: FailureKind,
    ) -> RecoveryPlan | None:
        """Build a concrete RecoveryPlan for a strategy, or None if the
        strategy can't be applied for this agent / kind (caller skips)."""
        if strategy is Strategy.ABORT:
            return RecoveryPlan(strategy=Strategy.ABORT)

        if strategy is Strategy.RETRY_SAME_MODEL_VARIANT_PROMPT:
            hint = _PROMPT_HINTS.get(kind)
            if hint is None:
                return None
            return RecoveryPlan(strategy=strategy, prompt_hint=hint)

        if strategy is Strategy.RETRY_DIFFERENT_MODEL_SAME_PROMPT:
            model = self._fallback_models.get(failed_agent)
            if not model:
                return None
            return RecoveryPlan(strategy=strategy, override_model=model)

        if strategy is Strategy.RETRY_SMALLER_MODEL_SHORTER_PROMPT:
            smaller = self._smaller_models.get(failed_agent)
            if not smaller:
                return None
            return RecoveryPlan(
                strategy=strategy,
                override_model=smaller,
                prompt_hint=_SMALLER_MODEL_HINT,
                max_tokens_cap=_SMALLER_MODEL_TOKEN_CAP,
            )

        if strategy is Strategy.REPLACE_WITH_PEER:
            peer = self._peer_replacements.get(failed_agent)
            if not peer:
                return None
            return RecoveryPlan(strategy=strategy, peer_agent=peer)

        return None

    # ---------- Phase 11 back-compat shim ----------

    def plan_retry(
        self,
        failed_agent_name: str,
        original_result: AgentResult,
        input: AgentInput,
    ) -> dict | None:
        """Phase 11 sequential-runner hook. Returns {"model": fallback} for
        one retry, or None to give up.

        Phase 13a–13b keep the externally-observable contract:
          - MODEL_ERROR (the old `*_llm_error` set)        -> retry with fallback
          - PARSE_ERROR (Phase 13b same-model addendum)    -> None here; the
            runner's plan_recovery loop handles it.
          - PRECONDITION_MISSING / UNRECOVERABLE           -> None.

        Phase 13g switches the sequential runner over to plan_recovery and
        this shim retires.
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
