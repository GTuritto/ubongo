from __future__ import annotations

import datetime
import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.memory import graph, store, vault  # noqa: E402


@pytest.fixture
def db(tmp_path: Path):
    store.set_db_path(tmp_path / "g.db")
    store.bootstrap()
    vault.set_vault_root(tmp_path / "vault")
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")
    vault.set_vault_root(None)


# --- parse_wikilinks --------------------------------------------------------

def test_parse_wikilinks_basic() -> None:
    assert vault.parse_wikilinks("see [[note-a]] and [[note-b]]") == ["note-a", "note-b"]


def test_parse_wikilinks_alias_and_heading() -> None:
    assert vault.parse_wikilinks("[[target|alias]] [[doc#section]]") == ["target", "doc"]


def test_parse_wikilinks_dedup_and_empty() -> None:
    assert vault.parse_wikilinks("[[x]] [[x]]") == ["x"]
    assert vault.parse_wikilinks("no links here") == []


# --- population from a daily note -------------------------------------------

def test_daily_note_populates_vault_links(db) -> None:
    note = vault.append_to_daily_note(
        datetime.date(2026, 6, 4), datetime.time(10, 0),
        "check [[caching-notes]] and [[ops]]", "ok", "casual",
    )
    src = str(note.relative_to(vault._vault_root()))
    assert store.vault_links_from(src) == ["caching-notes", "ops"]


# --- graph API --------------------------------------------------------------

def test_neighbors_and_backlinks(db) -> None:
    store.upsert_vault_link("a.md", "b")
    store.upsert_vault_link("a.md", "c")
    store.upsert_vault_link("z.md", "a.md")
    assert graph.outbound("a.md") == ["b", "c"]
    assert graph.backlinks("a.md") == ["z.md"]
    assert graph.neighbors("a.md") == ["b", "c", "z.md"]


def test_upsert_is_idempotent(db) -> None:
    store.upsert_vault_link("a.md", "b")
    store.upsert_vault_link("a.md", "b")
    assert store.vault_links_from("a.md") == ["b"]


def test_traverse_bounded(db) -> None:
    # a -> b -> c
    store.upsert_vault_link("a.md", "b.md")
    store.upsert_vault_link("b.md", "c.md")
    assert graph.traverse("a.md", depth=1) == ["b.md"]
    assert graph.traverse("a.md", depth=2) == ["b.md", "c.md"]
    assert graph.traverse("a.md", depth=0) == []
