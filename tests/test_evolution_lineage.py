from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.evolution import lineage  # noqa: E402
from ubongo.evolution.generator import Variant  # noqa: E402
from ubongo.memory import store  # noqa: E402


@pytest.fixture
def db(tmp_path: Path):
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")


def _variants(n: int) -> list[Variant]:
    return [
        Variant(strategy="paraphrase", text=f"variant {i}", metadata={"occurrence": i})
        for i in range(n)
    ]


# --- store accessors --------------------------------------------------------

def test_append_and_read_roundtrip(db) -> None:
    row_id = store.append_lineage_variant(
        target="persona:architect",
        parent_id=None,
        generation=1,
        variant_text="hello",
        variant_metadata={"strategy": "paraphrase", "k": "v"},
    )
    rows = store.lineage_for_target("persona:architect")
    assert len(rows) == 1
    assert rows[0]["id"] == row_id
    assert rows[0]["variant_text"] == "hello"
    assert rows[0]["variant_metadata"] == {"strategy": "paraphrase", "k": "v"}
    assert rows[0]["generation"] == 1
    assert rows[0]["parent_id"] is None


def test_lineage_for_target_filters_by_generation(db) -> None:
    store.append_lineage_variant(target="t", parent_id=None, generation=1,
                                 variant_text="a", variant_metadata=None)
    store.append_lineage_variant(target="t", parent_id=None, generation=2,
                                 variant_text="b", variant_metadata=None)
    assert len(store.lineage_for_target("t", generation=1)) == 1
    assert len(store.lineage_for_target("t")) == 2


def test_max_lineage_generation(db) -> None:
    assert store.max_lineage_generation("t") == 0
    store.append_lineage_variant(target="t", parent_id=None, generation=1,
                                 variant_text="a", variant_metadata=None)
    store.append_lineage_variant(target="t", parent_id=None, generation=3,
                                 variant_text="b", variant_metadata=None)
    assert store.max_lineage_generation("t") == 3


def test_active_lineage_id_none_then_set(db) -> None:
    assert store.active_lineage_id("persona:architect") is None
    lid = store.append_lineage_variant(target="persona:architect", parent_id=None,
                                       generation=1, variant_text="x",
                                       variant_metadata=None)
    store.connection().execute(
        "INSERT INTO active_evolutions (target, lineage_id, promoted_at) VALUES (?, ?, ?)",
        ("persona:architect", lid, store.now_iso()),
    )
    assert store.active_lineage_id("persona:architect") == lid


# --- record_variants --------------------------------------------------------

def test_record_variants_writes_generation_one(db) -> None:
    ids = lineage.record_variants("persona:architect", _variants(8))
    assert len(ids) == 8
    rows = store.lineage_for_target("persona:architect")
    assert len(rows) == 8
    assert {r["generation"] for r in rows} == {1}
    # strategy folded into metadata
    assert rows[0]["variant_metadata"]["strategy"] == "paraphrase"


def test_record_variants_increments_generation(db) -> None:
    lineage.record_variants("persona:architect", _variants(3))
    lineage.record_variants("persona:architect", _variants(3))
    gens = {r["generation"] for r in store.lineage_for_target("persona:architect")}
    assert gens == {1, 2}


def test_record_variants_parent_null_without_promotion(db) -> None:
    lineage.record_variants("persona:architect", _variants(2))
    rows = store.lineage_for_target("persona:architect")
    assert all(r["parent_id"] is None for r in rows)


def test_record_variants_parent_points_to_active(db) -> None:
    # Promote a row, then a new generation should parent to it.
    first = store.append_lineage_variant(target="persona:architect", parent_id=None,
                                         generation=1, variant_text="base",
                                         variant_metadata=None)
    store.connection().execute(
        "INSERT INTO active_evolutions (target, lineage_id, promoted_at) VALUES (?, ?, ?)",
        ("persona:architect", first, store.now_iso()),
    )
    lineage.record_variants("persona:architect", _variants(2))
    new_rows = [r for r in store.lineage_for_target("persona:architect") if r["id"] != first]
    assert new_rows and all(r["parent_id"] == first for r in new_rows)


def test_record_empty_variants_noop(db) -> None:
    assert lineage.record_variants("persona:architect", []) == []
    assert store.lineage_for_target("persona:architect") == []


def test_next_generation(db) -> None:
    assert lineage.next_generation("persona:casual") == 1
    lineage.record_variants("persona:casual", _variants(1))
    assert lineage.next_generation("persona:casual") == 2
