from __future__ import annotations

import logging
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


def _write_skill_with_prompts(root: Path) -> Path:
    skill_dir = root / "summarize-conversation"
    (skill_dir / "prompts").mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: summarize-conversation\n"
        "description: Recap.\n"
        "risk: low\n"
        "reversibility: reversible\n"
        "default_persona: operator\n"
        "prompts:\n"
        "  summarize: prompts/summarize.md\n"
        "---\n"
        "\n"
        "Skill body for summarize-conversation.\n",
        encoding="utf-8",
    )
    (skill_dir / "prompts" / "summarize.md").write_text(
        "Summarize the conversation in 3-5 sentences.\n",
        encoding="utf-8",
    )
    return skill_dir


def test_discovery_does_not_load_body(skills_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    _write_skill_with_prompts(skills_dir)
    caplog.set_level(logging.INFO, logger="ubongo.skills")
    skills.list_skills()
    body_logs = [r for r in caplog.records if r.message == "skill_body_loaded"]
    prompt_logs = [r for r in caplog.records if r.message == "skill_prompt_loaded"]
    assert body_logs == []
    assert prompt_logs == []


def test_body_loads_on_first_call_and_caches(
    skills_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_skill_with_prompts(skills_dir)
    caplog.set_level(logging.INFO, logger="ubongo.skills")

    first = skills.body("summarize-conversation")
    second = skills.body("summarize-conversation")

    assert "Skill body for summarize-conversation." in first
    assert "---" not in first.split("\n", 1)[0]
    assert first == second

    body_logs = [r for r in caplog.records if r.message == "skill_body_loaded"]
    assert len(body_logs) == 1


def test_prompt_loads_on_first_call_and_caches(
    skills_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_skill_with_prompts(skills_dir)
    caplog.set_level(logging.INFO, logger="ubongo.skills")

    first = skills.prompt("summarize-conversation", "summarize")
    second = skills.prompt("summarize-conversation", "summarize")

    assert "3-5 sentences" in first
    assert first == second

    prompt_logs = [r for r in caplog.records if r.message == "skill_prompt_loaded"]
    assert len(prompt_logs) == 1


def test_unknown_prompt_key_raises(skills_dir: Path) -> None:
    _write_skill_with_prompts(skills_dir)
    with pytest.raises(KeyError):
        skills.prompt("summarize-conversation", "phantom")


def test_missing_prompt_file_raises(skills_dir: Path) -> None:
    skill_dir = skills_dir / "broken"
    (skill_dir / "prompts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: broken\n"
        "description: Has prompt that doesn't exist.\n"
        "risk: low\n"
        "reversibility: reversible\n"
        "prompts:\n"
        "  missing: prompts/nope.md\n"
        "---\n"
        "\n"
        "body\n",
        encoding="utf-8",
    )
    with pytest.raises(FileNotFoundError):
        skills.prompt("broken", "missing")


def test_reload_clears_caches(
    skills_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    skill_dir = _write_skill_with_prompts(skills_dir)
    caplog.set_level(logging.INFO, logger="ubongo.skills")

    skills.body("summarize-conversation")
    skills.prompt("summarize-conversation", "summarize")

    # rewrite the body and the prompt
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: summarize-conversation\n"
        "description: Recap.\n"
        "risk: low\n"
        "reversibility: reversible\n"
        "prompts:\n"
        "  summarize: prompts/summarize.md\n"
        "---\n"
        "\n"
        "Brand new body.\n",
        encoding="utf-8",
    )
    (skill_dir / "prompts" / "summarize.md").write_text(
        "Brand new prompt.\n", encoding="utf-8"
    )

    # without reload, cache is still in effect
    assert "Brand new body." not in skills.body("summarize-conversation")
    assert "Brand new prompt." not in skills.prompt("summarize-conversation", "summarize")

    skills.reload()

    # after reload, new content is visible
    assert "Brand new body." in skills.body("summarize-conversation")
    assert "Brand new prompt." in skills.prompt("summarize-conversation", "summarize")

    body_logs = [r for r in caplog.records if r.message == "skill_body_loaded"]
    prompt_logs = [r for r in caplog.records if r.message == "skill_prompt_loaded"]
    assert len(body_logs) == 2
    assert len(prompt_logs) == 2


def test_resolve_pinned_beats_suggested(skills_dir: Path) -> None:
    _write_skill_with_prompts(skills_dir)
    resolved = skills.resolve(pinned="summarize-conversation", suggested=None)
    assert resolved is not None
    assert resolved.name == "summarize-conversation"


def test_resolve_falls_back_to_suggested(skills_dir: Path) -> None:
    _write_skill_with_prompts(skills_dir)
    resolved = skills.resolve(pinned=None, suggested="summarize-conversation")
    assert resolved is not None
    assert resolved.name == "summarize-conversation"


def test_resolve_unknown_falls_through(skills_dir: Path) -> None:
    _write_skill_with_prompts(skills_dir)
    resolved = skills.resolve(pinned="phantom", suggested="summarize-conversation")
    assert resolved is not None
    assert resolved.name == "summarize-conversation"


def test_resolve_returns_none_when_nothing_applies(skills_dir: Path) -> None:
    _write_skill_with_prompts(skills_dir)
    assert skills.resolve(pinned=None, suggested=None) is None
    assert skills.resolve(pinned="phantom", suggested=None) is None


# --- Code-review regression tests (2026-05-13) ---


def test_prompt_rejects_path_traversal(skills_dir: Path):
    """Regression for review finding #1: skill prompt paths must be confined to
    the skill directory. A malicious SKILL.md could otherwise read .env or
    other secrets and inject them into the LLM prompt."""
    skill_dir = skills_dir / "evil"
    (skill_dir).mkdir(parents=True)
    (skills_dir / "outside.txt").write_text("TOPSECRET", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: evil\n"
        "description: traversal test\n"
        "risk: low\n"
        "reversibility: reversible\n"
        "default_persona: operator\n"
        "prompts:\n"
        "  p: ../outside.txt\n"
        "---\n"
        "\n"
        "Body.\n",
        encoding="utf-8",
    )
    skills.reload()
    with pytest.raises(ValueError, match="escapes skill directory"):
        skills.prompt("evil", "p")


def test_prompt_rejects_absolute_path(skills_dir: Path):
    """A skill must not be able to declare an absolute path either."""
    skill_dir = skills_dir / "evil-abs"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: evil-abs\n"
        "description: absolute-path test\n"
        "risk: low\n"
        "reversibility: reversible\n"
        "default_persona: operator\n"
        "prompts:\n"
        "  p: /etc/passwd\n"
        "---\n"
        "\n"
        "Body.\n",
        encoding="utf-8",
    )
    skills.reload()
    with pytest.raises(ValueError, match="must be relative"):
        skills.prompt("evil-abs", "p")


def test_prompt_allows_legitimate_subdir_path(skills_dir: Path):
    """The fix must not break ordinary nested prompt files inside the skill dir."""
    skill_dir = skills_dir / "good"
    (skill_dir / "prompts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: good\n"
        "description: ok\n"
        "risk: low\n"
        "reversibility: reversible\n"
        "default_persona: operator\n"
        "prompts:\n"
        "  ok: prompts/ok.md\n"
        "---\n"
        "\n"
        "Body.\n",
        encoding="utf-8",
    )
    (skill_dir / "prompts" / "ok.md").write_text("legit", encoding="utf-8")
    skills.reload()
    assert skills.prompt("good", "ok") == "legit"
