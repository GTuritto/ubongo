from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.agents import personas  # noqa: E402
from ubongo.evolution import sandbox  # noqa: E402
from ubongo.memory import store  # noqa: E402
from ubongo.repl import (  # noqa: E402
    _HELP_COMMANDS,
    _EVALUATE_LIST_SENTINEL,
    _parse_evaluate_command,
    _render_evaluate,
    _render_evaluate_targets,
)


class _Fake:
    def __init__(self, text: str) -> None:
        self.text = text
        self.model = "fake"
        self.tokens_in = 10
        self.tokens_out = 20
        self.latency_ms = 42
        self.attempts = 1


@pytest.fixture
def db(tmp_path: Path):
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    personas.reload()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")


@pytest.fixture
def fake_llm(monkeypatch):
    def _fake(system_prompt, messages, model, max_tokens):
        if "evaluation judge" in system_prompt.lower():
            return _Fake(json.dumps({"quality": 0.8, "hallucination": 0.1, "would_user_correct": False}))
        return _Fake("a generated response")

    monkeypatch.setattr(sandbox, "complete", _fake)


def _seed_generation(target: str, n: int, strategy: str = "paraphrase") -> None:
    for i in range(n):
        store.append_lineage_variant(
            target=target, parent_id=None, generation=1,
            variant_text=f"variant {i} body", variant_metadata={"strategy": strategy},
        )


# --- parser -----------------------------------------------------------------

def test_parse_no_arg_is_list_sentinel() -> None:
    assert _parse_evaluate_command("/evaluate") == _EVALUATE_LIST_SENTINEL


def test_parse_target() -> None:
    assert _parse_evaluate_command("/evaluate persona:casual") == "persona:casual"


def test_parse_other_command_is_none() -> None:
    assert _parse_evaluate_command("/optimize persona:casual") is None


# --- list renderer ----------------------------------------------------------

def test_targets_list_empty_when_no_variants(db) -> None:
    assert "No evaluable targets" in _render_evaluate_targets()


def test_targets_list_shows_targets_with_variants(db) -> None:
    _seed_generation("persona:architect", 2)
    out = _render_evaluate_targets()
    assert "persona:architect" in out
    assert "persona:casual" not in out  # casual has no variants


# --- evaluate renderer ------------------------------------------------------

def test_evaluate_no_variants_prompts_optimize(db, fake_llm) -> None:
    out = _render_evaluate("persona:casual")
    assert "Run /optimize persona:casual first" in out


def test_evaluate_produces_leaderboard_and_persists(db, fake_llm) -> None:
    _seed_generation("persona:architect", 3)
    out = _render_evaluate("persona:architect")
    assert "Leaderboard for persona:architect" in out
    assert "fitness=" in out
    rows = store.evaluations_for_target("persona:architect")
    assert len(rows) == 3  # one evaluation row per variant
    # Leaderboard order matches fitness desc.
    fits = [r["fitness"] for r in rows]
    assert fits == sorted(fits, reverse=True)


def test_evaluate_budget_partial_results(db, fake_llm, monkeypatch):
    # Cap calls so only some variants are scored. samples_per_eval defaults to
    # 5 -> 10 calls/variant. With max_calls_per_hour low, expect skips.
    from ubongo.config import load_config
    cfg = load_config()
    monkeypatch.setitem(cfg["evolution"], "max_calls_per_hour", 10)
    monkeypatch.setitem(cfg["evolution"], "samples_per_eval", 5)
    _seed_generation("persona:architect", 3)
    out = _render_evaluate("persona:architect")
    # 10 calls / (5 samples * 2) = exactly 1 variant scored, 2 skipped.
    assert "skipped" in out
    rows = store.evaluations_for_target("persona:architect")
    assert 1 <= len(rows) < 3


def test_evaluate_unknown_target(db, fake_llm) -> None:
    out = _render_evaluate("persona:bogus")
    assert "Unknown target" in out


def test_help_mentions_evaluate() -> None:
    assert "/evaluate" in _HELP_COMMANDS
