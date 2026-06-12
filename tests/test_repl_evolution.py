from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.agents import personas  # noqa: E402
from ubongo.memory import evolution_state
from ubongo.memory import store  # noqa: E402
from ubongo import repl  # noqa: E402
from ubongo.repl import (  # noqa: E402
    _HELP_COMMANDS,
    _parse_evolution_command,
    _render_evolution_control,
    _render_evolution_status,
)


@pytest.fixture
def db(tmp_path: Path):
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    personas.reload()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")


# --- parser -----------------------------------------------------------------

def test_parse_no_arg_is_status() -> None:
    assert _parse_evolution_command("/evolution") == "status"


def test_parse_subcommands() -> None:
    assert _parse_evolution_command("/evolution pause") == "pause"
    assert _parse_evolution_command("/evolution resume") == "resume"
    assert _parse_evolution_command("/evolution off") == "off"


def test_parse_other_command_is_none() -> None:
    assert _parse_evolution_command("/evaluate persona:casual") is None


# --- control transitions ----------------------------------------------------

def test_pause_resume_off_set_status(db) -> None:
    _render_evolution_control("resume")
    # resume warns if disabled but still sets status.
    assert evolution_state.get_evolution_status() == "running"
    _render_evolution_control("pause")
    assert evolution_state.get_evolution_status() == "paused"
    _render_evolution_control("off")
    assert evolution_state.get_evolution_status() == "off"


def test_resume_warns_when_disabled(db, monkeypatch) -> None:
    import ubongo.config as cfg
    monkeypatch.setattr(cfg, "load_evolution", lambda *a, **k: {"enabled": False})
    out = _render_evolution_control("resume")
    assert "enabled is false" in out
    assert evolution_state.get_evolution_status() == "running"


# --- status renderer --------------------------------------------------------

def test_status_render_no_generations(db) -> None:
    out = _render_evolution_status()
    assert "Evolution loop: status=" in out
    assert "no generations yet" in out


def test_status_render_with_generation(db) -> None:
    lid = evolution_state.append_lineage_variant(
        target="persona:architect", parent_id=None, generation=1,
        variant_text="v", variant_metadata={"strategy": "paraphrase"},
    )
    evolution_state.append_evaluation(
        lineage_id=lid, sample_set="s", success_rate=0.8, cost=1, latency_ms=1,
        hallucination_rate=0, user_correction_rate=0, fitness=0.88,
    )
    out = _render_evolution_status()
    assert "gen 1" in out
    assert "best fitness=0.880" in out


def test_help_mentions_evolution() -> None:
    assert "/evolution" in _HELP_COMMANDS
