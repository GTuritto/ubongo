from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.agents import personas  # noqa: E402
from ubongo.evolution import selection  # noqa: E402
from ubongo.memory import evolution_state
from ubongo.memory import store  # noqa: E402


@pytest.fixture
def db(tmp_path: Path):
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    personas.reload()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")


def _run(target: str, gen: int, ended_at: str) -> None:
    rid = evolution_state.start_evolution_run(target=target, generation=gen)
    evolution_state.finish_evolution_run(rid, calls_spent=10, outcome="completed", ended_at=ended_at)


def test_next_target_no_runs_is_registry_order(db) -> None:
    assert selection.next_target() == "persona:architect"


def test_next_target_prefers_never_run(db) -> None:
    # architect has run; the others never have -> a never-run target wins.
    _run("persona:architect", 1, "2026-06-01T10:00:00.000Z")
    assert selection.next_target() == "persona:operator"


def test_next_target_oldest_among_all_run(db, monkeypatch) -> None:
    # Pin to the personas so "oldest among all-run" is exercised without the
    # never-run config targets (which sort first) stealing the pick.
    from ubongo.evolution import targets
    monkeypatch.setattr(targets, "evolvable_targets",
                        lambda: ["persona:architect", "persona:operator", "persona:casual"])
    _run("persona:architect", 1, "2026-06-01T10:00:00.000Z")
    _run("persona:operator", 1, "2026-06-01T09:00:00.000Z")  # oldest
    _run("persona:casual", 1, "2026-06-01T11:00:00.000Z")
    assert selection.next_target() == "persona:operator"


def test_survivors_top_k_by_fitness(db) -> None:
    ids = []
    for i, fit in enumerate([0.5, 0.9, 0.7]):
        lid = evolution_state.append_lineage_variant(
            target="persona:casual", parent_id=None, generation=1,
            variant_text=f"v{i}", variant_metadata={"strategy": "paraphrase"},
        )
        evolution_state.append_evaluation(
            lineage_id=lid, sample_set="s", success_rate=fit, cost=1, latency_ms=1,
            hallucination_rate=0, user_correction_rate=0, fitness=fit,
        )
        ids.append((lid, fit))
    survs = selection.survivors("persona:casual", 1, 2)
    assert len(survs) == 2
    assert survs[0]["fitness"] == 0.9
    assert survs[1]["fitness"] == 0.7


def test_survivors_empty_when_unevaluated(db) -> None:
    evolution_state.append_lineage_variant(
        target="persona:casual", parent_id=None, generation=1,
        variant_text="v", variant_metadata={"strategy": "paraphrase"},
    )
    assert selection.survivors("persona:casual", 1, 3) == []


def test_survivors_k_zero(db) -> None:
    assert selection.survivors("persona:casual", 1, 0) == []
