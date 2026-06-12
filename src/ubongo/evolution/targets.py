"""Evolvable-target registry (Phase 16b, generalized Phase 19).

A *target* is something the GP layer can mutate. Targets have a **kind**:

- ``prompt`` — the three persona prompts (``persona:architect|operator|casual``).
  The base is the persona body; ``variant_text`` holds an alternate body.
- ``config`` — routing rules (``routing:default``), per-workflow tool chains
  (``toolchain:<workflow>``), and the Repair retry config (``retry:repair``).
  The base is a serialized YAML snapshot of the live config section;
  ``variant_text`` holds an alternate serialized config.

``resolve_base`` returns the promoted active variant when one exists (the
``active_evolutions`` seam), else the live base. ``apply_variant`` parses and
**validates** a config variant; invalid variants are rejected so a malformed
config never reaches generation persistence or a promotion. The registry is
intentionally explicit (no auto-discovery) so the mutable surface stays small
and reviewable.
"""

from __future__ import annotations

from typing import Any

import yaml

from ubongo.agents import personas
from ubongo.memory import evolution_state
from ubongo.memory import store

PROMPT = "prompt"
CONFIG = "config"

_PERSONA_PREFIX = "persona:"
_ROUTING_TARGET = "routing:default"
_TOOLCHAIN_PREFIX = "toolchain:"
_RETRY_TARGET = "retry:repair"


class UnknownTargetError(ValueError):
    """Raised when a target string is not in the registry."""


class InvalidVariantError(ValueError):
    """Raised when a config variant fails to parse or validate."""


_PERSONA_PEERS: dict[str, str] = {
    "architect": "operator",
    "operator": "architect",
    "casual": "operator",
}


def _toolchain_workflows() -> list[str]:
    """The workflows that get an evolvable tool-chain target: those referenced
    by routing.yaml rules (the live auto-routed set), kept small + reviewable."""
    from ubongo import router

    routing = router._load_routing()
    names: list[str] = []
    for rule in routing.get("rules", []) or []:
        wf = rule.get("workflow")
        if wf and wf not in names:
            names.append(wf)
    default = routing.get("default_workflow")
    if default and default not in names:
        names.append(default)
    # only workflows that actually exist
    declared = set(router.workflow_names())
    return [n for n in names if n in declared]


def evolvable_targets() -> list[str]:
    """Every target string the GP layer may optimize."""
    out = [f"{_PERSONA_PREFIX}{name}" for name in personas.VALID_PERSONAS]
    out.append(_ROUTING_TARGET)
    out.extend(f"{_TOOLCHAIN_PREFIX}{wf}" for wf in _toolchain_workflows())
    out.append(_RETRY_TARGET)
    return out


def is_target(target: str) -> bool:
    return target in evolvable_targets()


def _require(target: str) -> None:
    if not is_target(target):
        raise UnknownTargetError(target)


def target_kind(target: str) -> str:
    """Return ``prompt`` or ``config`` for a registered target."""
    _require(target)
    return PROMPT if target.startswith(_PERSONA_PREFIX) else CONFIG


def _persona_name(target: str) -> str:
    return target[len(_PERSONA_PREFIX):]


# --- live config snapshots (the base for config targets) --------------------


def _live_routing() -> dict[str, Any]:
    from ubongo import router

    routing = router._load_routing()
    return {"rules": routing.get("rules", []), "default_workflow": routing.get("default_workflow", "casual_reply")}


def _live_toolchain(workflow: str) -> dict[str, Any]:
    from ubongo import router

    return {"workflow": workflow, "agents": list(router.workflow_agents(workflow))}


def _live_retry() -> dict[str, Any]:
    from ubongo.config import load_config

    return load_config().get("agents", {}).get("repair", {}) or {}


def _serialize(obj: Any) -> str:
    return yaml.safe_dump(obj, sort_keys=False, default_flow_style=False).rstrip()


def serialize_config(target: str) -> str:
    """The serialized YAML snapshot of a config target's live section."""
    _require(target)
    if target == _ROUTING_TARGET:
        return _serialize(_live_routing())
    if target.startswith(_TOOLCHAIN_PREFIX):
        return _serialize(_live_toolchain(target[len(_TOOLCHAIN_PREFIX):]))
    if target == _RETRY_TARGET:
        return _serialize(_live_retry())
    raise UnknownTargetError(target)


def resolve_base(target: str) -> str:
    """The base text the generator mutates from: the promoted active variant
    when one exists, else the live base (persona body or serialized config)."""
    _require(target)
    active_id = evolution_state.active_lineage_id(target)
    if active_id is not None:
        row = evolution_state.lineage_row(active_id)
        if row is not None:
            return row["variant_text"]
    if target_kind(target) == PROMPT:
        return personas.get(_persona_name(target)).body
    return serialize_config(target)


def peer_of(target: str) -> str | None:
    """Recombine partner (prompt targets only). Config targets have no peer."""
    _require(target)
    if target_kind(target) != PROMPT:
        return None
    peer_name = _PERSONA_PEERS.get(_persona_name(target))
    return f"{_PERSONA_PREFIX}{peer_name}" if peer_name else None


# --- config variant parsing + validation ------------------------------------


def apply_variant(target: str, variant_text: str) -> Any:
    """Parse + validate a variant. For prompt targets returns the text. For
    config targets parses the YAML and validates it against the live system
    (workflows exist, agents are registered, retry keys are known). Raises
    ``InvalidVariantError`` on any problem."""
    _require(target)
    if target_kind(target) == PROMPT:
        if not variant_text.strip():
            raise InvalidVariantError("empty prompt variant")
        return variant_text
    try:
        parsed = yaml.safe_load(variant_text)
    except yaml.YAMLError as exc:
        raise InvalidVariantError(f"variant is not valid YAML: {exc}") from exc
    if target == _ROUTING_TARGET:
        return _validate_routing(parsed)
    if target.startswith(_TOOLCHAIN_PREFIX):
        return _validate_toolchain(parsed)
    if target == _RETRY_TARGET:
        return _validate_retry(parsed)
    raise UnknownTargetError(target)


def _validate_routing(parsed: Any) -> dict:
    from ubongo import router

    if not isinstance(parsed, dict):
        raise InvalidVariantError("routing variant must be a mapping")
    rules = parsed.get("rules")
    default = parsed.get("default_workflow")
    if not isinstance(rules, list) or not rules:
        raise InvalidVariantError("routing variant needs a non-empty 'rules' list")
    declared = set(router.workflow_names())
    for rule in rules:
        if not isinstance(rule, dict) or "match" not in rule or "workflow" not in rule:
            raise InvalidVariantError("each routing rule needs 'match' and 'workflow'")
        if not isinstance(rule["match"], dict):
            raise InvalidVariantError("rule 'match' must be a mapping")
        if rule["workflow"] not in declared:
            raise InvalidVariantError(f"unknown workflow in rule: {rule['workflow']}")
    if default not in declared:
        raise InvalidVariantError(f"unknown default_workflow: {default}")
    return parsed


def _validate_toolchain(parsed: Any) -> dict:
    from ubongo import runner

    if not isinstance(parsed, dict) or "agents" not in parsed:
        raise InvalidVariantError("toolchain variant must be a mapping with 'agents'")
    agents = parsed["agents"]
    if not isinstance(agents, list) or not agents:
        raise InvalidVariantError("toolchain 'agents' must be a non-empty list")
    registry = runner.default_registry()
    composer_present = False
    for a in agents:
        if a not in registry:
            raise InvalidVariantError(f"unknown agent in toolchain: {a}")
        if getattr(registry[a], "composer", False):
            composer_present = True
    if not composer_present:
        raise InvalidVariantError("toolchain has no composer agent")
    return parsed


_KNOWN_RETRY_KEYS = {"max_attempts", "fallback_models", "smaller_models", "peer_replacements"}


def _validate_retry(parsed: Any) -> dict:
    from ubongo import runner

    if not isinstance(parsed, dict):
        raise InvalidVariantError("retry variant must be a mapping")
    unknown = set(parsed) - _KNOWN_RETRY_KEYS
    if unknown:
        raise InvalidVariantError(f"unknown retry keys: {sorted(unknown)}")
    peers = parsed.get("peer_replacements", {}) or {}
    if not isinstance(peers, dict):
        raise InvalidVariantError("peer_replacements must be a mapping")
    registry = runner.default_registry()
    for agent_name, peer in peers.items():
        if peer is not None and peer not in registry:
            raise InvalidVariantError(f"peer '{peer}' for '{agent_name}' is not a registered agent")
    ma = parsed.get("max_attempts")
    if ma is not None and (not isinstance(ma, int) or ma < 1):
        raise InvalidVariantError("max_attempts must be a positive int")
    return parsed
