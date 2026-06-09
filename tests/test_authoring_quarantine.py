from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import skills  # noqa: E402
from ubongo.authoring import quarantine  # noqa: E402
from ubongo.authoring.candidate import SkillCandidate  # noqa: E402
from ubongo.memory import store  # noqa: E402
from ubongo.skills import _parse_skill  # noqa: E402


@pytest.fixture
def env(tmp_path: Path):
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    quarantine.set_candidates_dir(tmp_path / "candidates")
    skills.set_skills_dir(tmp_path / "live_skills")  # empty live dir
    yield tmp_path
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")
    quarantine.set_candidates_dir(None)
    skills.set_skills_dir(None)


def _candidate(name="diff-notes", command=None) -> SkillCandidate:
    return SkillCandidate(
        name=name, description="summarize a git diff", risk="low",
        reversibility="reversible", default_persona="operator",
        body="Read the diff and summarize.", prompts={"draft": "Notes for {diff}"},
        command_template=command,
    )


def test_written_folder_is_a_valid_skill(env) -> None:
    folder = quarantine.write_candidate_folder(_candidate())
    assert (folder / "SKILL.md").exists()
    assert (folder / "prompts" / "draft.md").exists()
    # The materialized folder must satisfy the live skills parser.
    parsed = _parse_skill(folder)
    assert parsed.name == "diff-notes"
    assert parsed.prompts == {"draft": "prompts/draft.md"}


def test_command_skill_records_command_in_body(env) -> None:
    folder = quarantine.write_candidate_folder(_candidate(command="git diff --stat"))
    text = (folder / "SKILL.md").read_text(encoding="utf-8")
    assert "git diff --stat" in text


def test_quarantine_is_not_discoverable(env) -> None:
    quarantine.persist(_candidate())
    # The live skills registry scans a different dir, so the candidate is invisible.
    assert skills.list_skills() == []
    assert not skills.has("diff-notes")


def test_persist_creates_draft_row(env) -> None:
    row_id = quarantine.persist(_candidate())
    row = store.get_authored_skill(row_id)
    assert row is not None
    assert row["status"] == "draft"
    assert row["name"] == "diff-notes"
    assert row["generation"] == 1
    assert row["candidate"]["description"] == "summarize a git diff"
    assert row["quarantine_path"].endswith("diff-notes")


def test_generation_increments_per_name(env) -> None:
    quarantine.persist(_candidate())
    second = quarantine.persist(_candidate())
    assert store.get_authored_skill(second)["generation"] == 2
    assert store.max_authored_generation("diff-notes") == 2


def test_store_list_and_update(env) -> None:
    rid = quarantine.persist(_candidate())
    assert [r["id"] for r in store.authored_skills(status="draft")] == [rid]
    assert store.update_authored_skill(rid, quality=0.42)
    assert store.get_authored_skill(rid)["quality"] == pytest.approx(0.42)
    assert store.authored_skills(status="approved") == []
