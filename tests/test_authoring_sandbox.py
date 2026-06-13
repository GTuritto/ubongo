from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.authoring import sandbox  # noqa: E402
from ubongo.authoring.candidate import SkillCandidate  # noqa: E402
from ubongo.evaluation import CallBudget  # noqa: E402
from ubongo.llm import CompletionResult  # noqa: E402
from ubongo.memory import authoring_state
from ubongo.memory import store  # noqa: E402

_JUDGE = '{"quality": 0.8, "hallucination": 0.1, "would_user_correct": false}'


def _fake_complete(*, gen="A helpful response.", judge=_JUDGE):
    def _inner(**kwargs):
        user = kwargs["messages"][0]["content"]
        if user.startswith("Score the response"):
            return CompletionResult(text=judge, model="m", tokens_in=3, tokens_out=3,
                                    latency_ms=5, attempts=1)
        return CompletionResult(text=gen, model="m", tokens_in=10, tokens_out=10,
                                latency_ms=20, attempts=1)
    return _inner


@pytest.fixture(autouse=True)
def enable_eval(monkeypatch):
    monkeypatch.setenv("UBONGO_DISABLE_AUTHORING_EVAL", "0")


def _prompt_skill(name="tidy", command=None) -> SkillCandidate:
    return SkillCandidate(name=name, description="tidy some text", risk="low",
                          reversibility="reversible", default_persona=None,
                          body="Tidy the text.", prompts={}, command_template=command)


def test_off_switch_returns_none(monkeypatch) -> None:
    monkeypatch.setenv("UBONGO_DISABLE_AUTHORING_EVAL", "1")
    monkeypatch.setattr(sandbox, "complete", _fake_complete())
    assert sandbox.evaluate_candidate(_prompt_skill(), samples_per_eval=2) is None


def test_prompt_skill_scored(monkeypatch) -> None:
    monkeypatch.setattr(sandbox, "complete", _fake_complete())
    m = sandbox.evaluate_candidate(_prompt_skill(), samples_per_eval=2)
    assert m is not None
    assert m.command_ok is None
    assert m.probes == 2
    assert m.quality == pytest.approx(0.8)
    assert m.hallucination == pytest.approx(0.1)
    assert m.would_correct_rate == pytest.approx(0.0)


def test_command_skill_clean_dry_run(monkeypatch) -> None:
    monkeypatch.setattr(sandbox, "complete", _fake_complete())
    m = sandbox.evaluate_candidate(_prompt_skill(command="git diff --stat"),
                                   samples_per_eval=1)
    assert m is not None and m.command_ok == 1.0


def test_command_skill_failed_dry_run(monkeypatch) -> None:
    monkeypatch.setattr(sandbox, "complete", _fake_complete())
    m = sandbox.evaluate_candidate(_prompt_skill(command="git not-a-real-subcommand-zzz"),
                                   samples_per_eval=1)
    assert m is not None and m.command_ok == 0.0


def test_command_skill_refused_dry_run(monkeypatch) -> None:
    # A non-allowlisted command is refused by the shell sandbox -> command_ok 0.
    monkeypatch.setattr(sandbox, "complete", _fake_complete())
    m = sandbox.evaluate_candidate(_prompt_skill(command="rm -rf /"), samples_per_eval=1)
    assert m is not None and m.command_ok == 0.0


def test_budget_all_or_nothing(monkeypatch) -> None:
    monkeypatch.setattr(sandbox, "complete", _fake_complete())
    # 2 probes need 4 calls; a budget of 3 cannot cover them -> None.
    tight = CallBudget(3)
    assert sandbox.evaluate_candidate(_prompt_skill(), samples_per_eval=2, budget=tight) is None


def test_all_probes_dropped_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(sandbox, "complete", _fake_complete(gen="   "))  # empty generations
    assert sandbox.evaluate_candidate(_prompt_skill(), samples_per_eval=2) is None


def test_judge_parse_tolerates_fence(monkeypatch) -> None:
    fenced = f"```json\n{_JUDGE}\n```"
    monkeypatch.setattr(sandbox, "complete", _fake_complete(judge=fenced))
    m = sandbox.evaluate_candidate(_prompt_skill(), samples_per_eval=1)
    assert m is not None and m.quality == pytest.approx(0.8)


def test_evaluation_is_side_effect_free(tmp_path: Path, monkeypatch) -> None:
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    try:
        monkeypatch.setattr(sandbox, "complete", _fake_complete())
        sandbox.evaluate_candidate(_prompt_skill(), samples_per_eval=2)
        # The harness writes nothing durable.
        assert authoring_state.authored_skills() == []
    finally:
        store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")
