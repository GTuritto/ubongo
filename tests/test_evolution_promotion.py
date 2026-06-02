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
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    vault.set_vault_root(tmp_path / "vault")
    personas.reload()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")
    vault.set_vault_root(None)


def _seed(target, gen, fit, text="body", strat="prune") -> int:
    lid = store.append_lineage_variant(
        target=target, parent_id=None, generation=gen,
        variant_text=text, variant_metadata={"strategy": strat, "kind": "prompt"},
    )
    store.append_evaluation(
        lineage_id=lid, sample_set="s", success_rate=fit, cost=1, latency_ms=1,
        hallucination_rate=0, user_correction_rate=0, fitness=fit,
    )
    return lid


# --- proposer ---------------------------------------------------------------

def test_proposer_enqueues_when_champion_beats_baseline(db) -> None:
    _seed("persona:architect", 1, 0.9)
    pid = promotion.propose_if_better("persona:architect", 1)
    assert pid is not None
    assert len(store.open_pending_promotions()) == 1


def test_proposer_skips_when_open_promotion_exists(db) -> None:
    _seed("persona:architect", 1, 0.9)
    promotion.propose_if_better("persona:architect", 1)
    _seed("persona:architect", 2, 0.95)
    assert promotion.propose_if_better("persona:architect", 2) is None  # one already open


def test_proposer_skips_when_below_margin(db) -> None:
    # gen1 promoted-equivalent baseline; gen2 only marginally better -> no propose
    first = _seed("persona:casual", 1, 0.80)
    store.set_active_evolution("persona:casual", first)
    _seed("persona:casual", 2, 0.82)  # +0.02 < margin 0.05
    assert promotion.propose_if_better("persona:casual", 2) is None


def test_proposer_proposes_when_above_margin_over_active(db) -> None:
    first = _seed("persona:casual", 1, 0.80)
    store.set_active_evolution("persona:casual", first)
    _seed("persona:casual", 2, 0.90)  # +0.10 > margin
    assert promotion.propose_if_better("persona:casual", 2) is not None


# --- approve / reject / rollback --------------------------------------------

def test_approve_sets_active_and_audits(db) -> None:
    _seed("persona:architect", 1, 0.9)
    pid = promotion.propose_if_better("persona:architect", 1)
    d = promotion.approve(pid)
    assert d is not None and d.action == "approve"
    assert store.active_evolution("persona:architect") is not None
    assert store.get_pending_promotion(pid)["decision"] == "approved"
    assert vault.audit_log_path().exists()
    assert "approve" in vault.audit_log_path().read_text()


def test_reject_records_and_shrinks_queue(db) -> None:
    _seed("persona:architect", 1, 0.9)
    pid = promotion.propose_if_better("persona:architect", 1)
    assert len(store.open_pending_promotions()) == 1
    promotion.reject(pid)
    assert len(store.open_pending_promotions()) == 0
    assert store.active_evolution("persona:architect") is None  # reject does not promote


def test_rollback_clears_active(db) -> None:
    lid = _seed("persona:architect", 1, 0.9)
    store.set_active_evolution("persona:architect", lid)
    assert promotion.rollback("persona:architect") is True
    assert store.active_evolution("persona:architect") is None
    assert promotion.rollback("persona:architect") is False  # nothing to roll back


def test_approve_unknown_or_decided_is_none(db) -> None:
    assert promotion.approve(999) is None
    _seed("persona:architect", 1, 0.9)
    pid = promotion.propose_if_better("persona:architect", 1)
    promotion.approve(pid)
    assert promotion.approve(pid) is None  # already decided


def test_baseline_prefers_active_then_prior(db) -> None:
    assert promotion.baseline_fitness("persona:casual", 1) == 0.0  # nothing
    _seed("persona:casual", 1, 0.7)
    assert promotion.baseline_fitness("persona:casual", 2) == 0.7  # prior incumbent
    active = _seed("persona:casual", 1, 0.5)
    store.set_active_evolution("persona:casual", active)
    assert promotion.baseline_fitness("persona:casual", 2) == 0.5  # active wins
