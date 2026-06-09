from __future__ import annotations

import os

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.authoring.candidate import SkillCandidate  # noqa: E402
from ubongo.authoring.validation import CandidateInvalid, validate  # noqa: E402


def _candidate(**over) -> SkillCandidate:
    base = dict(name="good-skill", description="does a thing", risk="low",
                reversibility="reversible", default_persona=None, body="instructions",
                prompts={}, command_template=None)
    base.update(over)
    return SkillCandidate(**base)


def test_valid_prompt_skill_passes() -> None:
    out = validate(_candidate())
    assert out.name == "good-skill"
    assert out.risk == "low"


@pytest.mark.parametrize("bad", ["Bad Name", "../escape", "x/y", "UPPER", "", "a." * 25])
def test_bad_name_rejected(bad) -> None:
    with pytest.raises(CandidateInvalid):
        validate(_candidate(name=bad))


def test_missing_body_rejected() -> None:
    with pytest.raises(CandidateInvalid):
        validate(_candidate(body="  "))


def test_bad_risk_rejected() -> None:
    with pytest.raises(CandidateInvalid):
        validate(_candidate(risk="spicy"))


def test_bad_persona_rejected() -> None:
    with pytest.raises(CandidateInvalid):
        validate(_candidate(default_persona="wizard"))


def test_bad_prompt_key_rejected() -> None:
    with pytest.raises(CandidateInvalid):
        validate(_candidate(prompts={"Bad Key!": "body"}))


def test_empty_prompt_body_rejected() -> None:
    with pytest.raises(CandidateInvalid):
        validate(_candidate(prompts={"k": "   "}))


def test_command_skill_risk_floor_applied() -> None:
    # declared low/reversible, but a command forces medium/irreversible
    out = validate(_candidate(risk="low", reversibility="reversible",
                              command_template="git status"))
    assert out.risk == "medium"
    assert out.reversibility == "irreversible"


def test_command_skill_keeps_higher_declared_risk() -> None:
    out = validate(_candidate(risk="high", command_template="git status"))
    assert out.risk == "high"
    assert out.reversibility == "irreversible"


@pytest.mark.parametrize("cmd", [
    "rm -rf /",            # non-allowlisted program
    "cat /etc/passwd",    # path traversal / sensitive tree
    "git log | grep x",   # shell metacharacter
    "cat ../../secret",   # relative traversal
])
def test_command_skill_unsafe_command_rejected(cmd) -> None:
    with pytest.raises(CandidateInvalid):
        validate(_candidate(command_template=cmd))


def test_command_skill_allowlisted_command_passes() -> None:
    out = validate(_candidate(command_template="git diff --stat"))
    assert out.is_command_skill
