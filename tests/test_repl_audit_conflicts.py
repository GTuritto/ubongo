from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.memory import index_state
from ubongo.memory import store, vault  # noqa: E402
from ubongo.repl import (  # noqa: E402
    _HELP_COMMANDS,
)
from ubongo.memory.commands import (  # noqa: E402
    _parse_audit_command,
    _parse_conflicts_command,
    _render_audit,
    _render_conflicts_list,
    _render_conflicts_resolve,
)


@pytest.fixture
def db(tmp_path: Path):
    store.set_db_path(tmp_path / "ac.db")
    store.bootstrap()
    vault.set_vault_root(tmp_path / "vault")
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")
    vault.set_vault_root(None)


# --- /audit -----------------------------------------------------------------

def test_parse_audit() -> None:
    assert _parse_audit_command("/audit") == (None, 20)
    assert _parse_audit_command("/audit governance") == ("governance", 20)
    assert _parse_audit_command("/audit evolution 5") == ("evolution", 5)
    assert _parse_audit_command("/recall x") is None


def test_render_audit_empty(db) -> None:
    assert "No audit entries" in _render_audit(None, 20)


def test_render_audit_filtered(db) -> None:
    vault.append_audit_entry("governance", "**reject** x")
    vault.append_audit_entry("evolution", "**approve** y")
    out = _render_audit("governance", 20)
    assert "[governance]" in out and "[evolution]" not in out


# --- /conflicts -------------------------------------------------------------

def test_parse_conflicts() -> None:
    assert _parse_conflicts_command("/conflicts") == ("list", None, None)
    assert _parse_conflicts_command("/conflicts resolve 3 keep-mine") == ("resolve", 3, "keep-mine")
    assert _parse_conflicts_command("/conflicts resolve 3 bogus") == ("usage", None, None)
    assert _parse_conflicts_command("/conflicts resolve x merge") == ("usage", None, None)
    assert _parse_conflicts_command("/audit") is None


def test_conflicts_list_empty(db) -> None:
    assert "No open vault conflicts" in _render_conflicts_list()


def test_conflicts_list_and_resolve(db) -> None:
    cid = index_state.append_vault_conflict(path="daily/x.md", system_hash="aaa", disk_hash="bbb")
    assert "daily/x.md" in _render_conflicts_list()
    out = _render_conflicts_resolve(cid, "keep-theirs")
    assert "resolved" in out
    assert index_state.open_vault_conflicts() == []
    assert "[sync]" in vault.audit_log_path().read_text()


def test_resolve_unknown(db) -> None:
    assert "No open conflict" in _render_conflicts_resolve(999, "merge")


def test_keep_mine_notes_append_only(db) -> None:
    cid = index_state.append_vault_conflict(path="daily/x.md", system_hash="a", disk_hash="b")
    out = _render_conflicts_resolve(cid, "keep-mine")
    assert "append-only" in out


def test_help_mentions_audit_and_conflicts() -> None:
    assert "/audit" in _HELP_COMMANDS and "/conflicts" in _HELP_COMMANDS
