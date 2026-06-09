"""Candidate validation + the command-skill risk floor (Phase 1c).

`validate(candidate)` returns a *normalized* candidate or raises `CandidateInvalid`
with a human reason. It reuses the exact `skills._parse_skill` vocab (so a
candidate that passes here will parse once materialized) and, for a command
skill, the exact `sandbox.validate_command` contract (so a generated command
that would be refused at run time is refused at draft time). It also enforces
the risk floor: any command-bearing candidate is forced to risk>=medium /
irreversible, regardless of what the drafting model declared, so a self-authored
skill cannot mark itself low-risk to slip past governance.
"""

from __future__ import annotations

import re
from dataclasses import replace

from ubongo import sandbox
from ubongo.authoring.candidate import SkillCandidate
from ubongo.skills import PERSONA_VOCAB, REVERSIBILITY_VOCAB, RISK_VOCAB

# kebab-case slug usable as a directory name: no slashes, dots, or traversal.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")

# Risk ordering for the command-skill floor (medium is the floor).
_RISK_ORDER = ("low", "medium", "high", "destructive")
_RISK_FLOOR = "medium"


class CandidateInvalid(Exception):
    """A drafted candidate failed schema, sandbox, or naming validation."""


def _apply_command_risk_floor(candidate: SkillCandidate) -> SkillCandidate:
    """Force a command-bearing candidate to risk>=medium / irreversible."""
    if not candidate.is_command_skill:
        return candidate
    cur = candidate.risk if candidate.risk in _RISK_ORDER else "low"
    floored = cur
    if _RISK_ORDER.index(cur) < _RISK_ORDER.index(_RISK_FLOOR):
        floored = _RISK_FLOOR
    return replace(candidate, risk=floored, reversibility="irreversible")


def validate(candidate: SkillCandidate) -> SkillCandidate:
    """Validate and normalize a candidate. Raises CandidateInvalid on any breach.

    The returned candidate has the command-skill risk floor applied, so callers
    persist/materialize the normalized form.
    """
    name = (candidate.name or "").strip()
    if not _NAME_RE.match(name):
        raise CandidateInvalid(
            f"name {candidate.name!r} must be kebab-case (lowercase letters, digits, "
            "hyphens; <=40 chars; no slashes or dots)"
        )
    if not candidate.description or not candidate.description.strip():
        raise CandidateInvalid("description is required")
    if not candidate.body or not candidate.body.strip():
        raise CandidateInvalid("SKILL.md body is required")

    # Apply the floor BEFORE checking vocab, so a forced value is what we vet.
    normalized = _apply_command_risk_floor(replace(candidate, name=name))

    if normalized.risk not in RISK_VOCAB:
        raise CandidateInvalid(
            f"risk {normalized.risk!r} must be one of {sorted(RISK_VOCAB)}"
        )
    if normalized.reversibility not in REVERSIBILITY_VOCAB:
        raise CandidateInvalid(
            f"reversibility {normalized.reversibility!r} must be one of "
            f"{sorted(REVERSIBILITY_VOCAB)}"
        )
    if normalized.default_persona is not None and normalized.default_persona not in PERSONA_VOCAB:
        raise CandidateInvalid(
            f"default_persona {normalized.default_persona!r} must be one of "
            f"{sorted(PERSONA_VOCAB)} or null"
        )

    for key in normalized.prompts:
        if not _KEY_RE.match(key):
            raise CandidateInvalid(
                f"prompt key {key!r} must be a safe slug (lowercase letters, digits, "
                "'-' or '_'; <=40 chars)"
            )
        if not normalized.prompts[key].strip():
            raise CandidateInvalid(f"prompt {key!r} body is empty")

    # The whole point of the command floor: a command skill must pass the exact
    # run-time sandbox gate now, before it can ever be approved/registered.
    if normalized.is_command_skill:
        try:
            sandbox.validate_command(normalized.command_template)
        except sandbox.SandboxRefused as exc:
            raise CandidateInvalid(f"command template rejected by sandbox: {exc}") from None

    return normalized
