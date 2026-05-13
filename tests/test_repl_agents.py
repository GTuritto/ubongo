from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context, events, skills  # noqa: E402
from ubongo.memory import store, vault  # noqa: E402
from ubongo.repl import _render_agents_table  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")
    vault.set_vault_root(tmp_path / "vault")
    skills.set_skills_dir(None)
    skills.reload()
    context.reload()
    events.clear()
    yield
    events.clear()
    skills.set_skills_dir(None)
    skills.reload()
    context.reload()
    store.set_db_path(None)
    vault.set_vault_root(None)


def test_render_agents_table_includes_header():
    out = _render_agents_table()
    assert out.splitlines()[0] == "Registered agents:"


def test_render_agents_table_lists_research_and_memory():
    out = _render_agents_table()
    assert "research" in out
    assert "memory" in out


def test_render_agents_table_lists_all_three_personas():
    out = _render_agents_table()
    lines = out.splitlines()
    # Phase 10 rename: bare persona names, not `persona:<name>`.
    assert any(line.lstrip().startswith("architect ") for line in lines)
    assert any(line.lstrip().startswith("operator ") for line in lines)
    assert any(line.lstrip().startswith("casual ") for line in lines)


def test_render_agents_table_lists_evaluator_and_critic():
    out = _render_agents_table()
    assert "evaluator" in out
    assert "critic" in out


def test_render_agents_table_includes_role_and_model():
    out = _render_agents_table()
    lines = out.splitlines()
    # research line should include its role keyword and a non-empty model
    research_line = next(line for line in lines if line.lstrip().startswith("research"))
    assert "retrieval and synthesis" in research_line
    # column 3 is the model id; non-dash means a model was resolved
    assert "openrouter" in research_line or "test" in research_line


def test_render_agents_table_dash_when_no_model():
    out = _render_agents_table()
    memory_line = next(line for line in out.splitlines() if line.lstrip().startswith("memory"))
    # MemoryAgent.default_model == "" -> rendered as "—"
    assert "—" in memory_line
