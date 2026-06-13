from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.agents import personas  # noqa: E402
from ubongo.evolution import generator  # noqa: E402
from ubongo.memory import evolution_state
from ubongo.memory import store  # noqa: E402
from ubongo.repl import (  # noqa: E402
    _HELP_COMMANDS,
)
from ubongo.evolution.commands import (  # noqa: E402
    _OPTIMIZE_LIST_SENTINEL,
    _parse_optimize_command,
    _render_optimize,
    _render_optimize_targets,
)


class _FakeCompletion:
    def __init__(self, text: str) -> None:
        self.text = text
        self.model = "fake-model"
        self.tokens_in = self.tokens_out = self.latency_ms = self.attempts = 1


@pytest.fixture
def db(tmp_path: Path):
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    personas.reload()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")


@pytest.fixture
def fake_llm(monkeypatch):
    monkeypatch.setattr(
        generator, "complete",
        lambda system_prompt, messages, model, max_tokens: _FakeCompletion("a rewritten prompt"),
    )


# --- parser -----------------------------------------------------------------

def test_parse_no_arg_is_list_sentinel() -> None:
    assert _parse_optimize_command("/optimize") == _OPTIMIZE_LIST_SENTINEL
    assert _parse_optimize_command("/optimize   ") == _OPTIMIZE_LIST_SENTINEL


def test_parse_target() -> None:
    assert _parse_optimize_command("/optimize persona:architect") == "persona:architect"


def test_parse_other_command_returns_none() -> None:
    assert _parse_optimize_command("/mode list") is None


# --- list renderer ----------------------------------------------------------

def test_render_targets_lists_personas() -> None:
    out = _render_optimize_targets()
    assert "persona:architect" in out
    assert "persona:operator" in out
    assert "persona:casual" in out


# --- generate renderer ------------------------------------------------------

def test_optimize_generates_eight_rows(db, fake_llm) -> None:
    out = _render_optimize("persona:architect")
    assert "generation 1" in out
    rows = evolution_state.lineage_for_target("persona:architect")
    assert len(rows) == 8
    assert all(r["generation"] == 1 for r in rows)


def test_optimize_second_run_is_generation_two(db, fake_llm) -> None:
    _render_optimize("persona:casual")
    out = _render_optimize("persona:casual")
    assert "generation 2" in out
    gens = {r["generation"] for r in evolution_state.lineage_for_target("persona:casual")}
    assert gens == {1, 2}


def test_optimize_unknown_target_errors(db, fake_llm) -> None:
    out = _render_optimize("persona:bogus")
    assert "Unknown target" in out
    assert evolution_state.lineage_for_target("persona:bogus") == []


def test_help_mentions_optimize() -> None:
    assert "/optimize" in _HELP_COMMANDS
