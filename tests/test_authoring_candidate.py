from __future__ import annotations

import os

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.authoring import candidate  # noqa: E402
from ubongo.authoring.candidate import DraftError, SkillCandidate, draft_candidate  # noqa: E402
from ubongo.llm import CompletionResult, LLMError  # noqa: E402

_GOOD_JSON = (
    '{"name": "diff-notes", "description": "Summarize a git diff into release notes", '
    '"risk": "medium", "reversibility": "irreversible", "default_persona": "operator", '
    '"body": "Read the diff and write release notes.", '
    '"prompts": {"draft": "Write notes for: {diff}"}, '
    '"command_template": "git diff --stat"}'
)


def _fake_complete(text: str):
    def _inner(**kwargs):
        return CompletionResult(text=text, model="m", tokens_in=1, tokens_out=1,
                                latency_ms=1, attempts=1)
    return _inner


def test_draft_parses_plain_json(monkeypatch) -> None:
    monkeypatch.setattr(candidate, "complete", _fake_complete(_GOOD_JSON))
    c = draft_candidate("release notes from a diff")
    assert isinstance(c, SkillCandidate)
    assert c.name == "diff-notes"
    assert c.is_command_skill
    assert c.prompts["draft"].startswith("Write notes")
    assert c.metadata["source"] == "manual"


def test_draft_tolerates_code_fence(monkeypatch) -> None:
    fenced = f"```json\n{_GOOD_JSON}\n```"
    monkeypatch.setattr(candidate, "complete", _fake_complete(fenced))
    c = draft_candidate("x")
    assert c.name == "diff-notes"


def test_draft_tolerates_surrounding_prose(monkeypatch) -> None:
    noisy = f"Here is the skill:\n{_GOOD_JSON}\nHope that helps."
    monkeypatch.setattr(candidate, "complete", _fake_complete(noisy))
    c = draft_candidate("x")
    assert c.name == "diff-notes"


def test_prompt_skill_has_no_command(monkeypatch) -> None:
    j = '{"name": "tidy", "description": "d", "risk": "low", "reversibility": "reversible", "default_persona": null, "body": "b", "prompts": {}, "command_template": null}'
    monkeypatch.setattr(candidate, "complete", _fake_complete(j))
    c = draft_candidate("x")
    assert not c.is_command_skill
    assert c.command_template is None


def test_draft_empty_description_raises() -> None:
    with pytest.raises(DraftError):
        draft_candidate("   ")


def test_draft_unparseable_raises(monkeypatch) -> None:
    monkeypatch.setattr(candidate, "complete", _fake_complete("no json here at all"))
    with pytest.raises(DraftError):
        draft_candidate("x")


def test_draft_llm_error_raises(monkeypatch) -> None:
    def _boom(**kwargs):
        raise LLMError("down", cause=RuntimeError("x"))
    monkeypatch.setattr(candidate, "complete", _boom)
    with pytest.raises(DraftError):
        draft_candidate("x")


def test_roundtrip_dict() -> None:
    c = SkillCandidate(name="n", description="d", risk="low", reversibility="reversible",
                       default_persona="casual", body="b", prompts={"k": "v"})
    assert SkillCandidate.from_dict(c.to_dict()) == c
