from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import repl, skills  # noqa: E402
from ubongo.authoring import candidate, quarantine  # noqa: E402
from ubongo.commands import ReplState  # noqa: E402
from ubongo.llm import CompletionResult  # noqa: E402
from ubongo.memory import store, vault  # noqa: E402

_JSON = (
    '{"name": "diff-notes", "description": "release notes from a diff", '
    '"risk": "low", "reversibility": "reversible", "default_persona": "operator", '
    '"body": "summarize the diff", "prompts": {}, "command_template": "git diff --stat"}'
)


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    quarantine.set_candidates_dir(tmp_path / "candidates")
    skills.set_skills_dir(tmp_path / "live")
    monkeypatch.setattr(vault, "append_audit_entry", lambda *a, **k: None)
    yield tmp_path
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")
    quarantine.set_candidates_dir(None)
    skills.set_skills_dir(None)


def _state() -> ReplState:
    return ReplState(persona="architect", auto_mode=False, pending_skill=None,
                     pending_workflow=None)


def _fake(**kwargs):
    return CompletionResult(text=_JSON, model="m", tokens_in=1, tokens_out=1,
                            latency_ms=1, attempts=1)


def test_author_command_drafts_and_quarantines(env, monkeypatch) -> None:
    monkeypatch.setattr(candidate, "complete", _fake)
    out = repl._cmd_author("author release notes from a diff", _state())
    assert "diff-notes" in out
    assert "quarantined" in out.lower()
    assert "git diff --stat" in out
    # registered as a draft
    assert store.authored_skills(status="draft")


def test_author_command_usage_when_empty(env) -> None:
    out = repl._cmd_author("author", _state())
    assert "Usage" in out


def test_skill_candidates_lists_drafts(env, monkeypatch) -> None:
    monkeypatch.setattr(candidate, "complete", _fake)
    repl._cmd_author("author release notes", _state())
    listing = repl._cmd_skill_candidates("skill-candidates", _state())
    assert "diff-notes" in listing
    assert "draft" in listing


def test_skill_candidates_empty(env) -> None:
    out = repl._cmd_skill_candidates("skill-candidates", _state())
    assert "No authored skill candidates" in out


def test_commands_registered() -> None:
    assert repl.COMMANDS["author"].usage == "/author <description>"
    assert "skill-candidates" in repl.COMMANDS
