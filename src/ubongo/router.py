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
_WORKFLOWS_PATH = _REPO_ROOT / "config" / "workflows.yaml"

_DEFAULT_PERSONA = "casual"
_PERSONA_AGENT_PREFIX = "persona:"

_routing_cache: dict[str, Any] | None = None
_workflows_cache: dict[str, Any] | None = None


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


def _load_workflows() -> dict[str, Any]:
    global _workflows_cache
    if _workflows_cache is not None:
        return _workflows_cache
    if not _WORKFLOWS_PATH.exists():
        raise FileNotFoundError(f"workflows.yaml not found at {_WORKFLOWS_PATH}")
    with _WORKFLOWS_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    _workflows_cache = data
    return data


def reload() -> None:
    global _routing_cache, _workflows_cache
    _routing_cache = None
    _workflows_cache = None


def workflow_agents(name: str) -> tuple[str, ...]:
    """Return the agent list for a workflow name from workflows.yaml."""
    data = _load_workflows()
    workflows = data.get("workflows", {}) or {}
    wf = workflows.get(name)
    if wf is None:
        default = data.get("default_workflow", "casual_reply")
        wf = workflows.get(default, {})
        logger.warning(
            "router_unknown_workflow",
            extra={"workflow": name, "fallback": default},
        )
    agents = wf.get("agents") or [f"{_PERSONA_AGENT_PREFIX}{_DEFAULT_PERSONA}"]
    return tuple(agents)


def workflow_mode(name: str) -> str:
    data = _load_workflows()
    wf = (data.get("workflows", {}) or {}).get(name, {})
    return wf.get("mode", "sequential")


def workflow_persona(name: str) -> str:
    """Extract the persona name from the persona: agent inside the workflow."""
    for agent in workflow_agents(name):
        if agent.startswith(_PERSONA_AGENT_PREFIX):
            return agent[len(_PERSONA_AGENT_PREFIX):]
    return _DEFAULT_PERSONA


def _persona_for_workflow(workflow: str) -> str:
    return workflow_persona(workflow)


def _matches(rule_match: dict[str, Any], classification_dict: dict[str, Any]) -> bool:
    for key, expected in rule_match.items():
        if classification_dict.get(key) != expected:
            return False
    return True


def route_workflow(classification: Classification) -> str:
    """Map a Classification to a workflow name via routing.yaml rules."""
    routing = _load_routing()
    rules: list[dict] = routing.get("rules", []) or []
    default_workflow: str = routing.get("default_workflow", "casual_reply")

    cls_dict = asdict(classification)
    for rule in rules:
        match_block = rule.get("match", {}) or {}
        if _matches(match_block, cls_dict):
            return rule.get("workflow", default_workflow)
    return default_workflow


def route(classification: Classification) -> str:
    """Map a Classification to a persona name (via its workflow)."""
    return workflow_persona(route_workflow(classification))


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
