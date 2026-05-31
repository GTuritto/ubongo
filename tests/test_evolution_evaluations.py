from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.memory import store  # noqa: E402


@pytest.fixture
def db(tmp_path: Path):
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")


def _lineage(target: str, gen: int, text: str, strategy: str) -> int:
    return store.append_lineage_variant(
        target=target, parent_id=None, generation=gen,
        variant_text=text, variant_metadata={"strategy": strategy},
    )


def test_append_and_read_evaluation(db) -> None:
    lid = _lineage("persona:architect", 1, "body", "paraphrase")
    eid = store.append_evaluation(
        lineage_id=lid, sample_set="default-v1",
        success_rate=0.8, cost=120.0, latency_ms=55.0,
        hallucination_rate=0.1, user_correction_rate=0.0, fitness=0.91,
    )
    assert eid > 0
    rows = store.evaluations_for_target("persona:architect")
    assert len(rows) == 1
    r = rows[0]
    assert r["lineage_id"] == lid
    assert r["fitness"] == 0.91
    assert r["strategy"] == "paraphrase"
    assert r["generation"] == 1


def test_evaluations_ordered_fitness_desc_then_lineage_asc(db) -> None:
    l1 = _lineage("persona:casual", 1, "a", "paraphrase")
    l2 = _lineage("persona:casual", 1, "b", "prune")
    l3 = _lineage("persona:casual", 1, "c", "expand")
    store.append_evaluation(lineage_id=l1, sample_set="s", success_rate=0.5,
                            cost=1, latency_ms=1, hallucination_rate=0,
                            user_correction_rate=0, fitness=0.50)
    store.append_evaluation(lineage_id=l2, sample_set="s", success_rate=0.9,
                            cost=1, latency_ms=1, hallucination_rate=0,
                            user_correction_rate=0, fitness=0.90)
    # l3 ties l2 on fitness -> lower lineage_id (l2) ranks first.
    store.append_evaluation(lineage_id=l3, sample_set="s", success_rate=0.9,
                            cost=1, latency_ms=1, hallucination_rate=0,
                            user_correction_rate=0, fitness=0.90)
    rows = store.evaluations_for_target("persona:casual")
    assert [r["lineage_id"] for r in rows] == [l2, l3, l1]


def test_evaluations_generation_filter(db) -> None:
    l1 = _lineage("persona:operator", 1, "a", "paraphrase")
    l2 = _lineage("persona:operator", 2, "b", "prune")
    store.append_evaluation(lineage_id=l1, sample_set="s", success_rate=0.5,
                            cost=1, latency_ms=1, hallucination_rate=0,
                            user_correction_rate=0, fitness=0.5)
    store.append_evaluation(lineage_id=l2, sample_set="s", success_rate=0.6,
                            cost=1, latency_ms=1, hallucination_rate=0,
                            user_correction_rate=0, fitness=0.6)
    assert len(store.evaluations_for_target("persona:operator", generation=1)) == 1
    assert len(store.evaluations_for_target("persona:operator")) == 2


def test_latest_evaluation_for_lineage(db) -> None:
    lid = _lineage("persona:architect", 1, "x", "expand")
    assert store.latest_evaluation_for_lineage(lid) is None
    store.append_evaluation(lineage_id=lid, sample_set="s", success_rate=0.5,
                            cost=1, latency_ms=1, hallucination_rate=0,
                            user_correction_rate=0, fitness=0.5)
    store.append_evaluation(lineage_id=lid, sample_set="s", success_rate=0.7,
                            cost=1, latency_ms=1, hallucination_rate=0,
                            user_correction_rate=0, fitness=0.7)
    latest = store.latest_evaluation_for_lineage(lid)
    assert latest is not None
    assert latest["fitness"] == 0.7  # most recent
