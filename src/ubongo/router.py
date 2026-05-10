from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from ubongo.classifier import Classification
from ubongo.config import load_config

logger = logging.getLogger("ubongo.router")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ROUTING_PATH = _REPO_ROOT / "config" / "routing.yaml"

# Phase-3 shortcut: maps each workflow defined in workflows.yaml to its persona.
# Phase 8 replaces this with a workflows.yaml reader that also exposes agents,
# mode, and risk; for Phase 3 only the persona field is needed.
_WORKFLOW_TO_PERSONA: dict[str, str] = {
    "technical_deep": "architect",
    "quick_action": "operator",
    "casual_reply": "casual",
    "supportive_reply": "casual",
    "research_brief": "architect",
    "coding_session": "architect",
    "debate_then_synthesize": "architect",
    "speculative_brief": "operator",
}

_DEFAULT_PERSONA = "casual"

_routing_cache: dict[str, Any] | None = None


def _load_routing() -> dict[str, Any]:
    global _routing_cache
    if _routing_cache is not None:
        return _routing_cache
    if not _ROUTING_PATH.exists():
        raise FileNotFoundError(f"routing.yaml not found at {_ROUTING_PATH}")
    with _ROUTING_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    _routing_cache = data
    return data


def reload() -> None:
    global _routing_cache
    _routing_cache = None


def _persona_for_workflow(workflow: str) -> str:
    persona = _WORKFLOW_TO_PERSONA.get(workflow)
    if persona is None:
        logger.warning(
            "router_unknown_workflow",
            extra={"workflow": workflow, "fallback_persona": _DEFAULT_PERSONA},
        )
        return _DEFAULT_PERSONA
    return persona


def _matches(rule_match: dict[str, Any], classification_dict: dict[str, Any]) -> bool:
    for key, expected in rule_match.items():
        if classification_dict.get(key) != expected:
            return False
    return True


def route(classification: Classification) -> str:
    """Map a Classification to a persona name via routing.yaml rules."""
    routing = _load_routing()
    rules: list[dict] = routing.get("rules", []) or []
    default_workflow: str = routing.get("default_workflow", "casual_reply")

    cls_dict = asdict(classification)
    for rule in rules:
        match_block = rule.get("match", {}) or {}
        if _matches(match_block, cls_dict):
            return _persona_for_workflow(rule.get("workflow", default_workflow))

    return _persona_for_workflow(default_workflow)


def _confidence_threshold() -> float:
    config = load_config()
    governance = config.get("governance", {}) or {}
    raw = governance.get("confidence_threshold_for_auto", 0.7)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.7


def apply_hysteresis(current_persona: str, suggested: str, confidence: float) -> str:
    """Decide whether to switch to a new persona, given current state and confidence."""
    if suggested == current_persona:
        return current_persona
    threshold = _confidence_threshold()
    if confidence < threshold:
        return current_persona
    return suggested
