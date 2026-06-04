from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.agents import personas  # noqa: E402
from ubongo.memory import store, vault  # noqa: E402
from ubongo.repl import _HELP_COMMANDS, _parse_recall_command, _render_recall  # noqa: E402


@pytest.fixture
def db(tmp_path: Path):
    store.set_db_path(tmp_path / "r.db")
    store.bootstrap()
    vault.set_vault_root(tmp_path / "vault")
    personas.reload()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")
    vault.set_vault_root(None)


def _seed_conversation() -> int:
    cid = store.start_conversation("casual")
    store.upsert_session(active_persona="casual", current_conversation_id=cid)
    store.append_message(cid, "user", "we discussed caching earlier", persona="casual")
    return cid


# --- parser -----------------------------------------------------------------

def test_parse_no_query() -> None:
    assert _parse_recall_command("/recall") == ""


def test_parse_with_query() -> None:
    assert _parse_recall_command("/recall caching layer") == "caching layer"


def test_parse_other_command() -> None:
    assert _parse_recall_command("/evolution status") is None


# --- renderer ---------------------------------------------------------------

def test_render_no_conversation(db) -> None:
    assert "No conversation yet" in _render_recall("")


def test_render_recency_and_degrades_without_embeddings(db) -> None:
    _seed_conversation()
    out = _render_recall("caching")
    assert "recency window" in out
    # embeddings disabled by default (conftest off-switch) -> recency-only note
    assert "recency only" in out


def test_render_shows_vault_neighbors(db) -> None:
    cid = _seed_conversation()
    store.upsert_vault_link("daily/2026-06-04.md", "caching-notes")
    out = _render_recall("caching")
    assert "vault graph" in out


def test_help_mentions_recall() -> None:
    assert "/recall" in _HELP_COMMANDS
