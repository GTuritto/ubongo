"""Repair Agent: detects and recovers failed agent runs.

Phase 11 ships a minimum-viable single-retry policy with a model fallback.
Phase 13 will broaden to peer-agent replacement, rollback, and richer
recovery plans.

The Repair Agent itself does not run as part of any workflow; it is
consulted synchronously by the WorkflowRunner when an agent reports
ok=False. plan_retry returns either:
  - None  -> do not retry
  - {"model": <fallback-model>}  -> rerun the failed agent with this model
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ubongo.agents.base import AgentInput, AgentResult
from ubongo.config import load_config

if TYPE_CHECKING:
    from ubongo.master import Context

logger = logging.getLogger("ubongo.agents.repair")

# Errors that are worth retrying with a different model. Sandbox refusals
# (execution_refused) are by design, not transient. Memory write failures
# need DB-side rollback (Phase 13), not a retry.
_RETRYABLE_ERRORS: frozenset[str] = frozenset({
    "persona_llm_error",
    "research_llm_error",
    "evaluator_llm_error",
    "critic_llm_error",
    "coding_llm_error",
})

_NEVER_RETRY_AGENTS: frozenset[str] = frozenset({"memory", "execution"})


class RepairAgent:
    name = "repair"
    role = "detects and recovers failed agent runs (single-retry v0.1)"
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
    # Repair v0.1: the runner calls plan_retry directly. This keeps Repair
    # registry-discoverable without putting it into any workflow.agents list.
    def run(self, input: AgentInput, context: "Context") -> AgentResult:
        return AgentResult(
            text="", ok=True, model=None,
            tokens_in=0, tokens_out=0, latency_ms=0,
            metadata={"note": "RepairAgent.run is a no-op; runner calls plan_retry"},
        )

    def plan_retry(
        self,
        failed_agent_name: str,
        original_result: AgentResult,
        input: AgentInput,
    ) -> dict | None:
        """Return a retry plan, or None to give up.

        Phase 11 v0.1: single-retry with a model fallback for LLM-error
        classes; no retry for memory/execution; no retry on unknown errors.
        """
        if failed_agent_name in _NEVER_RETRY_AGENTS:
            logger.info(
                "repair_no_retry_agent_kind",
                extra={"agent": failed_agent_name, "error": original_result.error},
            )
            return None
        if original_result.error not in _RETRYABLE_ERRORS:
            logger.info(
                "repair_no_retry_error_kind",
                extra={"agent": failed_agent_name, "error": original_result.error},
            )
            return None
        fallback = self._fallback_models.get(failed_agent_name)
        if not fallback:
            logger.info(
                "repair_no_fallback_model",
                extra={"agent": failed_agent_name},
            )
            return None
        logger.info(
            "repair_retry_planned",
            extra={"agent": failed_agent_name, "fallback_model": fallback,
                   "original_error": original_result.error},
        )
        return {"model": fallback}


default_repair_agent = RepairAgent()
