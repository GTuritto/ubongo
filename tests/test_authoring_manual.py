from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import skills  # noqa: E402
from ubongo.authoring import candidate, manual, quarantine  # noqa: E402
from ubongo.authoring.manual import AuthoringError, author_skill  # noqa: E402
from ubongo.llm import CompletionResult  # noqa: E402
from ubongo.memory import store, vault  # noqa: E402

_CMD_JSON = (
    '{"name": "diff-notes", "description": "release notes from a diff", '
    '"risk": "low", "reversibility": "reversible", "default_persona": "operator", '
    '"body": "summarize the diff", "prompts": {"draft": "notes for {diff}"}, '
    '"command_template": "git diff --stat"}'
)


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    quarantine.set_candidates_dir(tmp_path / "candidates")
    skills.set_skills_dir(tmp_path / "live")
    audits: list[tuple[str, str]] = []
    monkeypatch.setattr(vault, "append_audit_entry",
                        lambda cat, line, **kw: audits.append((cat, line)))
    yield tmp_path, audits
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")
    quarantine.set_candidates_dir(None)
    skills.set_skills_dir(None)


def _fake(text: str):
    def _inner(**kwargs):
        return CompletionResult(text=text, model="m", tokens_in=1, tokens_out=1,
                                latency_ms=1, attempts=1)
    return _inner


def test_author_skill_end_to_end(env, monkeypatch) -> None:
    _, audits = env
    monkeypatch.setattr(candidate, "complete", _fake(_CMD_JSON))
    outcome = author_skill("release notes from a diff")
    assert outcome.candidate.name == "diff-notes"
    # command skill -> risk floor applied
    assert outcome.candidate.risk == "medium"
    assert outcome.candidate.reversibility == "irreversible"
    # persisted as draft, generation 1
    row = store.get_authored_skill(outcome.candidate_id)
    assert row["status"] == "draft" and row["generation"] == 1
    # audited under the authoring category
    assert audits and audits[0][0] == "authoring"
    # still not discoverable
    assert not skills.has("diff-notes")


def test_author_skill_invalid_draft_raises(env, monkeypatch) -> None:
    bad = _CMD_JSON.replace('"diff-notes"', '"Bad Name"')
    monkeypatch.setattr(candidate, "complete", _fake(bad))
    with pytest.raises(AuthoringError):
        author_skill("x")


def test_author_skill_unsafe_command_raises(env, monkeypatch) -> None:
    bad = _CMD_JSON.replace("git diff --stat", "rm -rf /")
    monkeypatch.setattr(candidate, "complete", _fake(bad))
    with pytest.raises(AuthoringError):
        author_skill("x")
