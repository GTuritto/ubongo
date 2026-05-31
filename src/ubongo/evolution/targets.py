"""Evolvable-target registry (Phase 16b).

A *target* is something the GP layer can mutate. In Phase 16 the only targets
are the three persona prompts, addressed as ``persona:<name>``. A target string
resolves to its **base text** — the prompt the generator mutates from.

The base is the persona file body when the target is unpromoted (always the
case in Phase 16, since no promotions exist yet). The ``active_evolutions`` seam
is checked first so that, once Phase 19 promotes a variant, ``resolve_base``
returns the promoted text without any change here.

Routing rules, tool chains, and retry strategies become evolvable targets in
Phase 19; the registry is intentionally explicit (no auto-discovery) so the set
of mutable things stays small and reviewable.
"""

from __future__ import annotations

from ubongo.agents import personas
from ubongo.memory import store


class UnknownTargetError(ValueError):
    """Raised when a target string is not in the registry."""


_PERSONA_PREFIX = "persona:"

# Recombine partner for each persona target: the natural neighbouring voice.
# Mirrors settings.yaml::agents.repair.peer_replacements for the personas.
_PERSONA_PEERS: dict[str, str] = {
    "architect": "operator",
    "operator": "architect",
    "casual": "operator",
}


def evolvable_targets() -> list[str]:
    """Return every target string the GP layer may optimize, sorted."""
    return [f"{_PERSONA_PREFIX}{name}" for name in personas.VALID_PERSONAS]


def is_target(target: str) -> bool:
    return target in evolvable_targets()


def _require(target: str) -> None:
    if not is_target(target):
        raise UnknownTargetError(target)


def _persona_name(target: str) -> str:
    return target[len(_PERSONA_PREFIX):]


def resolve_base(target: str) -> str:
    """Return the base prompt text the generator mutates from.

    The promoted active variant when one exists (Phase 19+), else the persona
    file body. Raises ``UnknownTargetError`` for unregistered targets.
    """
    _require(target)
    active_id = store.active_lineage_id(target)
    if active_id is not None:
        rows = store.lineage_for_target(target)
        for row in rows:
            if row["id"] == active_id:
                return row["variant_text"]
    # Unpromoted (Phase 16 always lands here): the persona file body.
    return personas.get(_persona_name(target)).body


def peer_of(target: str) -> str | None:
    """Return a sibling target to recombine with, or None when there is no peer.

    A ``recombine`` strategy with no peer is skipped by the generator and the
    round-robin advances, so the variant count is still met.
    """
    _require(target)
    peer_name = _PERSONA_PEERS.get(_persona_name(target))
    if peer_name is None:
        return None
    return f"{_PERSONA_PREFIX}{peer_name}"
