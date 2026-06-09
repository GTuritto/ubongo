from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import repl, skills  # noqa: E402
from ubongo.authoring import candidate, promotion, quarantine  # noqa: E402
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
    promotion.set_backups_dir(tmp_path / "backups")
    skills.set_skills_dir(tmp_path / "live")
    monkeypatch.setattr(vault, "append_audit_entry", lambda *a, **k: None)
    yield tmp_path
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")
    quarantine.set_candidates_dir(None)
    promotion.set_backups_dir(None)
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


def test_author_shows_quality_when_eval_enabled(env, monkeypatch) -> None:
    from ubongo.authoring import sandbox as eval_sandbox
    monkeypatch.setenv("UBONGO_DISABLE_AUTHORING_EVAL", "0")
    monkeypatch.setattr(candidate, "complete", _fake)

    def _eval_complete(**kwargs):
        if kwargs["messages"][0]["content"].startswith("Score the response"):
            text = '{"quality": 0.75, "hallucination": 0.1, "would_user_correct": false}'
        else:
            text = "ok"
        return CompletionResult(text=text, model="m", tokens_in=2, tokens_out=2,
                                latency_ms=4, attempts=1)
    monkeypatch.setattr(eval_sandbox, "complete", _eval_complete)

    out = repl._cmd_author("author release notes from a diff", _state())
    assert "quality:" in out
    # listing should also show the score
    listing = repl._cmd_skill_candidates("skill-candidates", _state())
    assert "quality=" in listing


def _draft(state) -> int:
    repl._cmd_author("author summarize a git diff into notes", state)
    return store.authored_skills(status="draft")[0]["id"]


def test_gate_approve_then_rollback(env, monkeypatch) -> None:
    monkeypatch.setattr(candidate, "complete", _fake)
    st = _state()
    cid = _draft(st)
    out = repl._cmd_skill_candidates(f"skill-candidates approve {cid}", st)
    assert "Approved" in out and "now in /skills" in out
    assert skills.has("diff-notes")
    out2 = repl._cmd_skill_candidates("skill-candidates rollback diff-notes", st)
    assert "Rolled back" in out2
    assert not skills.has("diff-notes")


def test_gate_reject(env, monkeypatch) -> None:
    monkeypatch.setattr(candidate, "complete", _fake)
    st = _state()
    cid = _draft(st)
    out = repl._cmd_skill_candidates(f"skill-candidates reject {cid}", st)
    assert "Rejected" in out
    assert store.get_authored_skill(cid)["status"] == "rejected"
    assert not skills.has("diff-notes")


def test_gate_usage_and_errors(env) -> None:
    assert "Usage" in repl._cmd_skill_candidates("skill-candidates approve", _state())
    assert "Usage" in repl._cmd_skill_candidates("skill-candidates approve notanint", _state())
    assert "Cannot do that" in repl._cmd_skill_candidates("skill-candidates approve 999", _state())
    assert "Cannot do that" in repl._cmd_skill_candidates("skill-candidates rollback nope", _state())


def test_listing_shows_collision_diff(env, monkeypatch) -> None:
    monkeypatch.setattr(candidate, "complete", _fake)
    st = _state()
    # approve a first version so a live skill exists
    cid = _draft(st)
    repl._cmd_skill_candidates(f"skill-candidates approve {cid}", st)
    # draft a second version of the same name; listing should show a diff
    _draft(st)
    listing = repl._cmd_skill_candidates("skill-candidates", st)
    assert "would overwrite live 'diff-notes'" in listing


def test_authoring_status_and_control(env) -> None:
    out = repl._cmd_authoring("authoring", _state())
    assert "Authoring daemon:" in out and "paused" in out
    repl._cmd_authoring("authoring resume", _state())
    assert store.get_authoring_status() == "running"
    repl._cmd_authoring("authoring pause", _state())
    assert store.get_authoring_status() == "paused"
    repl._cmd_authoring("authoring off", _state())
    assert store.get_authoring_status() == "off"


def test_commands_registered() -> None:
    assert repl.COMMANDS["author"].usage == "/author <description>"
    assert "skill-candidates" in repl.COMMANDS
    assert "authoring" in repl.COMMANDS
