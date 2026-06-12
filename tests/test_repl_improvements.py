from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.agents import personas  # noqa: E402
from ubongo.evolution import promotion  # noqa: E402
from ubongo.memory import evolution_state
from ubongo.memory import store, vault  # noqa: E402
from ubongo.repl import (  # noqa: E402
    _HELP_COMMANDS,
)
from ubongo.evolution.commands import (  # noqa: E402
    _parse_improvements_command,
    _render_improvements_action,
    _render_improvements_list,
)


@pytest.fixture
def db(tmp_path: Path):
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    vault.set_vault_root(tmp_path / "vault")
    personas.reload()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")
    vault.set_vault_root(None)


def _seed_and_propose(target="persona:casual", fit=0.9) -> int:
    lid = evolution_state.append_lineage_variant(
        target=target, parent_id=None, generation=1,
        variant_text="candidate body for the variant", variant_metadata={"strategy": "prune", "kind": "prompt"},
    )
    evolution_state.append_evaluation(
        lineage_id=lid, sample_set="s", success_rate=fit, cost=1, latency_ms=1,
        hallucination_rate=0, user_correction_rate=0, fitness=fit,
    )
    return promotion.propose_if_better(target, 1)


# --- parser -----------------------------------------------------------------

def test_parse_list() -> None:
    assert _parse_improvements_command("/improvements") == ("list", None)


def test_parse_approve_reject() -> None:
    assert _parse_improvements_command("/improvements approve 5") == ("approve", 5)
    assert _parse_improvements_command("/improvements reject 7") == ("reject", 7)


def test_parse_rollback() -> None:
    assert _parse_improvements_command("/improvements rollback persona:casual") == ("rollback", "persona:casual")


def test_parse_bad_id_is_usage() -> None:
    assert _parse_improvements_command("/improvements approve abc") == ("usage", None)
    assert _parse_improvements_command("/improvements approve") == ("usage", None)


def test_parse_other_command_none() -> None:
    assert _parse_improvements_command("/evolution status") is None


# --- list renderer ----------------------------------------------------------

def test_list_empty(db) -> None:
    assert "No pending improvements" in _render_improvements_list()


def test_list_shows_delta_and_diff(db) -> None:
    _seed_and_propose()
    out = _render_improvements_list()
    assert "persona:casual" in out
    assert "fitness" in out
    assert "candidate" in out  # diff header / body present


# --- actions ----------------------------------------------------------------

def test_approve_action(db) -> None:
    pid = _seed_and_propose()
    out = _render_improvements_action("approve", pid)
    assert "Approved" in out and "Live swap" in out
    assert evolution_state.active_evolution("persona:casual") is not None


def test_reject_action(db) -> None:
    pid = _seed_and_propose()
    out = _render_improvements_action("reject", pid)
    assert "Rejected" in out
    assert evolution_state.open_pending_promotions() == []


def test_rollback_action(db) -> None:
    pid = _seed_and_propose()
    promotion.approve(pid)
    out = _render_improvements_action("rollback", "persona:casual")
    assert "Rolled back" in out
    assert evolution_state.active_evolution("persona:casual") is None


def test_approve_unknown(db) -> None:
    assert "No open promotion" in _render_improvements_action("approve", 999)


def test_help_mentions_improvements() -> None:
    assert "/improvements" in _HELP_COMMANDS
