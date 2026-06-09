from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import skills  # noqa: E402
from ubongo.authoring import candidate, manual, quarantine  # noqa: E402
from ubongo.authoring import sandbox as eval_sandbox  # noqa: E402
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


def test_author_skill_eval_disabled_leaves_quality_none(env, monkeypatch) -> None:
    # conftest sets UBONGO_DISABLE_AUTHORING_EVAL=1, so /author skips evaluation.
    monkeypatch.setattr(candidate, "complete", _fake(_CMD_JSON))
    outcome = author_skill("x")
    assert outcome.quality is None
    assert store.get_authored_skill(outcome.candidate_id)["quality"] is None


def test_author_skill_safe_at_info_logging(env, monkeypatch, caplog) -> None:
    # Regression: the authoring loggers used extra={"name": ...}, which collides
    # with the reserved LogRecord.name and raises KeyError once logging is at INFO
    # (the live REPL configures INFO; the suite defaults to WARNING, which
    # short-circuits logger.info before the record is built). Exercise the whole
    # path at INFO so any reserved-key collision is caught.
    import logging

    monkeypatch.setenv("UBONGO_DISABLE_AUTHORING_EVAL", "0")
    monkeypatch.setattr(candidate, "complete", _fake(_CMD_JSON))

    def _eval_complete(**kwargs):
        if kwargs["messages"][0]["content"].startswith("Score the response"):
            text = '{"quality": 0.7, "hallucination": 0.2, "would_user_correct": false}'
        else:
            text = "A response."
        return CompletionResult(text=text, model="m", tokens_in=4, tokens_out=4,
                                latency_ms=8, attempts=1)
    monkeypatch.setattr(eval_sandbox, "complete", _eval_complete)

    with caplog.at_level(logging.INFO):  # builds every LogRecord -> would surface the collision
        outcome = author_skill("release notes from a diff")
    assert outcome.candidate_id > 0


def test_author_skill_records_quality_when_eval_enabled(env, monkeypatch) -> None:
    _, _audits = env
    monkeypatch.setenv("UBONGO_DISABLE_AUTHORING_EVAL", "0")
    monkeypatch.setattr(candidate, "complete", _fake(_CMD_JSON))

    def _eval_complete(**kwargs):
        if kwargs["messages"][0]["content"].startswith("Score the response"):
            text = '{"quality": 0.7, "hallucination": 0.2, "would_user_correct": false}'
        else:
            text = "A response."
        return CompletionResult(text=text, model="m", tokens_in=4, tokens_out=4,
                                latency_ms=8, attempts=1)
    monkeypatch.setattr(eval_sandbox, "complete", _eval_complete)

    outcome = author_skill("release notes from a diff")
    assert outcome.quality is not None and 0.0 <= outcome.quality <= 1.0
    assert store.get_authored_skill(outcome.candidate_id)["quality"] == pytest.approx(outcome.quality)
