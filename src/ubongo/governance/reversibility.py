"""Reversibility scoring (Phase 14d).

A turn is `irreversible` when it can mutate state outside Ubongo's own
recoverable memory. In v0.1 that means exactly one thing: the workflow runs
the `execution` agent (constrained-bash, which can touch the filesystem) or
the `connector` agent (external MCP tool calls — they happened; candidate 20,
ADR-0016), or it is pinned to a skill whose frontmatter declares `reversibility: irreversible`
(today: `constrained-bash`). Every other turn just produces text and is
`reversible`.

This is intentionally a thin module. It is the seam Phase 15 (sandbox + the
approval gate on `execution`) and Phase 19/20 (memory-mutating agents) build
on; the matrix only needs a typed verdict.
"""

from __future__ import annotations

from enum import Enum

from ubongo import skills


class Reversibility(str, Enum):
    REVERSIBLE = "reversible"
    IRREVERSIBLE = "irreversible"


def score_reversibility(workflow) -> Reversibility:
    """Return whether this workflow's turn is reversible."""
    agents = getattr(workflow, "agents", ()) or ()
    if "execution" in agents or "connector" in agents:
        return Reversibility.IRREVERSIBLE

    skill_name = getattr(workflow, "skill_name", None)
    if skill_name and skills.has(skill_name):
        if skills.get(skill_name).reversibility == "irreversible":
            return Reversibility.IRREVERSIBLE

    return Reversibility.REVERSIBLE
