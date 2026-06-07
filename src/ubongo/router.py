from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from ubongo.classifier import Classification
from ubongo.config import load_config, load_governance

logger = logging.getLogger("ubongo.router")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ROUTING_PATH = _REPO_ROOT / "config" / "routing.yaml"
_WORKFLOWS_PATH = _REPO_ROOT / "config" / "workflows.yaml"

_DEFAULT_PERSONA = "casual"
# Phase 10: Persona Agents live in the registry under bare names.
_PERSONA_AGENT_NAMES: tuple[str, ...] = ("architect", "operator", "casual")

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


# Phase 19: in-memory overrides for config evaluation (set via config_override),
# and live-swap reads from active_evolutions. Precedence for effective config:
# eval override > active promotion > file.
_routing_override: dict[str, Any] | None = None
_toolchain_override: dict[str, list] | None = None


def _promoted_config(target: str) -> Any | None:
    """Parsed promoted config for a target, or None (unpromoted / no DB)."""
    from ubongo.memory import store

    if not store.is_connected():
        return None
    active = store.active_evolution(target)
    if not active:
        return None
    try:
        return yaml.safe_load(active["variant_text"])
    except yaml.YAMLError:
        return None


def _effective_routing() -> dict[str, Any]:
    """Routing config: eval override > promoted > file."""
    if _routing_override is not None:
        return _routing_override
    promoted = _promoted_config("routing:default")
    if isinstance(promoted, dict) and promoted.get("rules"):
        return promoted
    return _load_routing()


def _effective_agents(name: str) -> tuple[str, ...] | None:
    """Promoted/overridden agent list for a workflow, or None to fall back to
    the file."""
    if _toolchain_override and name in _toolchain_override:
        return tuple(_toolchain_override[name])
    promoted = _promoted_config(f"toolchain:{name}")
    if isinstance(promoted, dict) and isinstance(promoted.get("agents"), list) and promoted["agents"]:
        return tuple(promoted["agents"])
    return None


class config_override:
    """Context manager that temporarily forces routing and/or per-workflow tool
    chains, for side-effect-free config evaluation. Restores on exit."""

    def __init__(self, *, routing: dict | None = None, toolchain: dict | None = None):
        self._routing = routing
        self._toolchain = toolchain
        self._saved: tuple = ()

    def __enter__(self):
        global _routing_override, _toolchain_override
        self._saved = (_routing_override, _toolchain_override)
        if self._routing is not None:
            _routing_override = self._routing
        if self._toolchain is not None:
            _toolchain_override = self._toolchain
        return self

    def __exit__(self, *exc):
        global _routing_override, _toolchain_override
        _routing_override, _toolchain_override = self._saved
        return False


def reload() -> None:
    global _routing_cache, _workflows_cache
    _routing_cache = None
    _workflows_cache = None


def workflow_agents(name: str) -> tuple[str, ...]:
    """Return the agent list for a workflow name from workflows.yaml.

    Phase 19: a promoted or eval-overridden tool chain for this workflow wins
    over the file.
    """
    effective = _effective_agents(name)
    if effective is not None:
        return effective
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
    agents = wf.get("agents") or [_DEFAULT_PERSONA]
    return tuple(agents)


def workflow_mode(name: str) -> str:
    """Return the execution mode for a workflow. Falls back to 'sequential'
    if the workflow declares an unknown mode (with a warning)."""
    from ubongo.runner import KNOWN_MODES

    data = _load_workflows()
    wf = (data.get("workflows", {}) or {}).get(name, {})
    mode = wf.get("mode", "sequential")
    if mode not in KNOWN_MODES:
        logger.warning(
            "router_unknown_mode_fallback",
            extra={"workflow": name, "declared_mode": mode, "fallback": "sequential"},
        )
        return "sequential"
    return mode


def workflow_rounds(name: str) -> int | None:
    """Phase 12d: optional `rounds` field on debate-mode workflows."""
    data = _load_workflows()
    wf = (data.get("workflows", {}) or {}).get(name, {})
    val = wf.get("rounds")
    if val is None:
        return None
    try:
        n = int(val)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def workflow_timeout_s(name: str) -> int | None:
    """Phase 12e: optional `timeout_s` field on speculative-mode workflows."""
    data = _load_workflows()
    wf = (data.get("workflows", {}) or {}).get(name, {})
    val = wf.get("timeout_s")
    if val is None:
        return None
    try:
        n = int(val)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def workflow_names() -> list[str]:
    """Phase 12g: enumerate all declared workflow names (for `/mode list`)."""
    data = _load_workflows()
    return sorted((data.get("workflows", {}) or {}).keys())


def workflow_persona(name: str) -> str:
    """Return the persona-agent name from the workflow's agent list."""
    for agent in workflow_agents(name):
        if agent in _PERSONA_AGENT_NAMES:
            return agent
    return _DEFAULT_PERSONA


def workflow_evaluate(name: str) -> bool:
    """Phase 10: per-workflow flag that appends `evaluator` to agents."""
    data = _load_workflows()
    wf = (data.get("workflows", {}) or {}).get(name, {})
    return bool(wf.get("evaluate", False))


def _persona_for_workflow(workflow: str) -> str:
    return workflow_persona(workflow)


def _matches(rule_match: dict[str, Any], classification_dict: dict[str, Any]) -> bool:
    for key, expected in rule_match.items():
        if classification_dict.get(key) != expected:
            return False
    return True


def route_workflow(classification: Classification) -> str:
    """Map a Classification to a workflow name via routing.yaml rules.

    Phase 19: a promoted or eval-overridden routing config wins over the file.
    """
    routing = _effective_routing()
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
    """Minimum classifier confidence to switch persona in /auto mode.

    Phase 14 moved this from `settings.yaml::governance` into
    `governance.yaml::thresholds.auto_route_min_confidence`.
    """
    thresholds = load_governance().get("thresholds", {}) or {}
    raw = thresholds.get("auto_route_min_confidence", 0.7)
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


# --------------------------------------------------------------------------
# Phase 08: one deep planning seam. `plan_workflow` collapses the routing +
# config assembly master.plan used to do across 7+ separate calls into a single
# validated WorkflowPlan. The persona-model and skill resolution stay in master
# (they read the persona/skill registries, not config); master maps WorkflowPlan
# -> Workflow by adding model + skill_name.
# --------------------------------------------------------------------------

# Persona -> its default workflow when routing does not pick one (or auto is off).
_PERSONA_DEFAULT_WORKFLOW: dict[str, str] = {
    "architect": "technical_deep",
    "operator": "quick_action",
    "casual": "casual_reply",
}


@dataclass(frozen=True)
class WorkflowPlan:
    """The config-derived shape of a turn's workflow, before master adds the
    persona model and resolved skill. Router-owned so the router need not import
    master.Workflow (which would be a cycle)."""

    workflow_name: str
    persona: str                      # chosen, post-hysteresis / pending override
    agents: tuple[str, ...]           # evaluator already appended where applicable
    mode: str
    rounds: int | None
    timeout_s: int | None
    # Routing telemetry for master's "classify" log (None when auto is off).
    suggested_workflow: str | None
    suggested_persona: str | None


def _resolve_workflow_name(
    chosen_persona: str,
    suggested_workflow: str | None,
    auto_mode: bool,
) -> str:
    """Decide which workflow to run.

    auto_mode + hysteresis kept the suggested persona  -> use suggested workflow.
    auto_mode + hysteresis flipped to a different one  -> persona's default.
    auto_mode off                                       -> persona's default.
    """
    if auto_mode and suggested_workflow is not None:
        if workflow_persona(suggested_workflow) == chosen_persona:
            return suggested_workflow
    return _PERSONA_DEFAULT_WORKFLOW.get(chosen_persona, "casual_reply")


def _validate_plan_shape(mode: str, agents: tuple[str, ...]) -> None:
    """Validate the structural mode/agents invariants at plan time — the same
    shape rules the runner enforces at execute time, surfaced earlier with a
    clearer error. The runner keeps its raises as a registry-aware backstop
    (it also checks that named agents exist and that the evaluator has rank())."""
    if mode == "competitive" and (not agents or agents[-1] != "evaluator"):
        raise ValueError(
            f"competitive workflows must end with 'evaluator'; got {list(agents)!r}"
        )
    if mode == "debate" and len(agents) < 3:
        raise ValueError(
            "debate workflows need at least 3 entries: [debater_a, debater_b, synthesizer]"
        )
    if mode == "speculative" and len(agents) < 2:
        raise ValueError("speculative workflows need at least [cheap, strong] in agents")


def plan_workflow(
    classification: Classification,
    *,
    current_persona: str,
    auto_mode: bool,
    pending_workflow: str | None,
) -> WorkflowPlan:
    """Assemble the validated WorkflowPlan for a turn from config.

    Owns: routing (`route_workflow`), persona hysteresis, the `/mode`
    pending-workflow override, workflow-name resolution, the agent list +
    evaluator append, mode/rounds/timeout reads, and structural validation.
    """
    suggested_workflow: str | None = None
    suggested_persona: str | None = None
    chosen = current_persona

    if auto_mode:
        suggested_workflow = route_workflow(classification)
        suggested_persona = workflow_persona(suggested_workflow)
        chosen = apply_hysteresis(current_persona, suggested_persona, classification.confidence)

    # Phase 12g: /mode <workflow> overrides routing for the next turn; its
    # persona is honored verbatim (overrides hysteresis).
    if pending_workflow and pending_workflow in workflow_names():
        workflow_name = pending_workflow
        chosen = workflow_persona(workflow_name)
    else:
        workflow_name = _resolve_workflow_name(chosen, suggested_workflow, auto_mode)

    agents = list(workflow_agents(workflow_name))
    mode = workflow_mode(workflow_name)
    # Phase 12b: competitive mode carries its own trailing evaluator as part of
    # the mode contract; skip the auto-append to avoid a duplicate.
    if workflow_evaluate(workflow_name) and mode != "competitive":
        agents.append("evaluator")
    agents_tuple = tuple(agents)
    _validate_plan_shape(mode, agents_tuple)

    return WorkflowPlan(
        workflow_name=workflow_name,
        persona=chosen,
        agents=agents_tuple,
        mode=mode,
        rounds=workflow_rounds(workflow_name),
        timeout_s=workflow_timeout_s(workflow_name),
        suggested_workflow=suggested_workflow,
        suggested_persona=suggested_persona,
    )
