from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.agents import personas  # noqa: E402
from ubongo.evolution import promotion  # noqa: E402
from ubongo.memory import store, vault  # noqa: E402


@pytest.fixture
def db(tmp_path: Path):
    store.set_db_path(tmp_path / "a.db")
    store.bootstrap()
    vault.set_vault_root(tmp_path / "vault")
    personas.reload()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")
    vault.set_vault_root(None)


def test_unified_path_is_audit_md(db) -> None:
    assert vault.audit_log_path().name == "audit.md"


def test_categories_written_and_filtered(db) -> None:
    vault.append_audit_entry("governance", "**reject** risk=high")
    vault.append_audit_entry("evolution", "**approve** persona:casual")
    vault.append_audit_entry("sync", "ingested external edit to daily/x.md")
    assert len(vault.audit_tail("governance")) == 1
    assert len(vault.audit_tail("evolution")) == 1
    assert len(vault.audit_tail("sync")) == 1
    assert len(vault.audit_tail()) == 3  # all
    assert "[governance]" in vault.audit_tail("governance")[0]


def test_tail_limit(db) -> None:
    for i in range(10):
        vault.append_audit_entry("sync", f"event {i}")
    assert len(vault.audit_tail(limit=3)) == 3
    assert "event 9" in vault.audit_tail(limit=3)[-1]


def test_phase19_append_audit_redirects_to_unified(db) -> None:
    # the back-compat shim routes evolution promotion rows into audit.md
    lid = store.append_lineage_variant(
        target="persona:casual", parent_id=None, generation=1,
        variant_text="body", variant_metadata={"strategy": "prune"},
    )
    store.append_evaluation(lineage_id=lid, sample_set="s", success_rate=0.9, cost=1,
                            latency_ms=1, hallucination_rate=0, user_correction_rate=0, fitness=0.9)
    pid = promotion.propose_if_better("persona:casual", 1)
    promotion.approve(pid)
    assert vault.audit_log_path().name == "audit.md"
    assert "[evolution]" in vault.audit_log_path().read_text()


def test_empty_audit(db) -> None:
    assert vault.audit_tail() == []
