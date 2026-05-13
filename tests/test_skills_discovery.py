from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import skills  # noqa: E402


@pytest.fixture
def skills_dir(tmp_path: Path):
    skills.set_skills_dir(tmp_path)
    yield tmp_path
    skills.set_skills_dir(None)


def _write_skill(root: Path, name: str, frontmatter: str, body: str = "Skill body.") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")
    return skill_dir


def test_empty_dir_yields_empty_registry(skills_dir: Path) -> None:
    assert skills.list_skills() == []


def test_single_skill_discovered(skills_dir: Path) -> None:
    _write_skill(
        skills_dir,
        "summarize-conversation",
        "name: summarize-conversation\n"
        "description: Recap the conversation.\n"
        "risk: low\n"
        "reversibility: reversible\n",
    )
    found = skills.list_skills()
    assert len(found) == 1
    s = found[0]
    assert s.name == "summarize-conversation"
    assert s.description == "Recap the conversation."
    assert s.risk == "low"
    assert s.reversibility == "reversible"
    assert s.default_persona is None
    assert s.prompts == {}


def test_optional_fields_parsed(skills_dir: Path) -> None:
    _write_skill(
        skills_dir,
        "summarize-conversation",
        "name: summarize-conversation\n"
        "description: Recap.\n"
        "risk: low\n"
        "reversibility: reversible\n"
        "default_persona: operator\n"
        "prompts:\n"
        "  summarize: prompts/summarize.md\n",
    )
    s = skills.get("summarize-conversation")
    assert s.default_persona == "operator"
    assert s.prompts == {"summarize": "prompts/summarize.md"}


def test_missing_required_field_raises(skills_dir: Path) -> None:
    _write_skill(
        skills_dir,
        "broken",
        "name: broken\n"
        "description: Missing risk.\n"
        "reversibility: reversible\n",
    )
    with pytest.raises(ValueError, match="risk"):
        skills.list_skills()


def test_invalid_risk_raises(skills_dir: Path) -> None:
    _write_skill(
        skills_dir,
        "bad-risk",
        "name: bad-risk\n"
        "description: Wrong risk.\n"
        "risk: catastrophic\n"
        "reversibility: reversible\n",
    )
    with pytest.raises(ValueError, match="risk"):
        skills.list_skills()


def test_invalid_reversibility_raises(skills_dir: Path) -> None:
    _write_skill(
        skills_dir,
        "bad-rev",
        "name: bad-rev\n"
        "description: Wrong reversibility.\n"
        "risk: low\n"
        "reversibility: maybe\n",
    )
    with pytest.raises(ValueError, match="reversibility"):
        skills.list_skills()


def test_invalid_default_persona_raises(skills_dir: Path) -> None:
    _write_skill(
        skills_dir,
        "bad-persona",
        "name: bad-persona\n"
        "description: Wrong persona.\n"
        "risk: low\n"
        "reversibility: reversible\n"
        "default_persona: oracle\n",
    )
    with pytest.raises(ValueError, match="default_persona"):
        skills.list_skills()


def test_directories_without_skill_md_are_ignored(skills_dir: Path) -> None:
    (skills_dir / "not-a-skill").mkdir()
    _write_skill(
        skills_dir,
        "real",
        "name: real\n"
        "description: A real skill.\n"
        "risk: low\n"
        "reversibility: reversible\n",
    )
    names = [s.name for s in skills.list_skills()]
    assert names == ["real"]


def test_get_unknown_raises(skills_dir: Path) -> None:
    with pytest.raises(KeyError):
        skills.get("phantom")


def test_has_returns_bool(skills_dir: Path) -> None:
    _write_skill(
        skills_dir,
        "summarize-conversation",
        "name: summarize-conversation\n"
        "description: Recap.\n"
        "risk: low\n"
        "reversibility: reversible\n",
    )
    assert skills.has("summarize-conversation") is True
    assert skills.has("phantom") is False


def test_production_constrained_bash_skill_loads() -> None:
    """Phase 11b: the shipped constrained-bash skill must load with the
    declared frontmatter (risk=medium, reversibility=irreversible)."""
    skills.set_skills_dir(None)  # reset to production config/skills/
    skills.reload()
    try:
        s = skills.get("constrained-bash")
        assert s.risk == "medium"
        assert s.reversibility == "irreversible"
        assert s.default_persona == "operator"
        assert "run" in s.prompts
    finally:
        skills.set_skills_dir(None)
        skills.reload()
