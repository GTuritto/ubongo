from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.agents import personas  # noqa: E402
from ubongo.evolution import targets  # noqa: E402
from ubongo.memory import evolution_state
from ubongo.memory import store  # noqa: E402


@pytest.fixture
def db(tmp_path: Path):
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    personas.reload()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")


def test_evolvable_targets_include_personas_and_config(db) -> None:
    ts = targets.evolvable_targets()
    # Phase 16 personas...
    for p in ("persona:architect", "persona:operator", "persona:casual"):
        assert p in ts
    # ...plus the Phase 19 config targets.
    assert "routing:default" in ts
    assert "retry:repair" in ts
    assert any(t.startswith("toolchain:") for t in ts)


def test_target_kinds(db) -> None:
    assert targets.target_kind("persona:architect") == targets.PROMPT
    assert targets.target_kind("routing:default") == targets.CONFIG
    assert targets.target_kind("retry:repair") == targets.CONFIG


def test_is_target(db) -> None:
    assert targets.is_target("persona:architect")
    assert targets.is_target("routing:default")  # now a config target (Phase 19)
    assert not targets.is_target("persona:bogus")
    assert not targets.is_target("nonsense:x")


def test_resolve_base_returns_persona_body(db) -> None:
    base = targets.resolve_base("persona:architect")
    assert isinstance(base, str)
    assert base.strip()
    # The body must match what the persona loader exposes.
    assert base == personas.get("architect").body


def test_resolve_base_unknown_target_raises(db) -> None:
    with pytest.raises(targets.UnknownTargetError):
        targets.resolve_base("persona:bogus")


def test_resolve_base_prefers_promoted_active_variant(db) -> None:
    # Simulate a Phase-19 promotion: a lineage row + an active_evolutions entry.
    lineage_id = evolution_state.append_lineage_variant(
        target="persona:architect",
        parent_id=None,
        generation=1,
        variant_text="PROMOTED architect prompt",
        variant_metadata={"strategy": "paraphrase"},
    )
    conn = store.connection()
    conn.execute(
        "INSERT INTO active_evolutions (target, lineage_id, promoted_at) VALUES (?, ?, ?)",
        ("persona:architect", lineage_id, store.now_iso()),
    )
    assert targets.resolve_base("persona:architect") == "PROMOTED architect prompt"


def test_peer_of() -> None:
    assert targets.peer_of("persona:architect") == "persona:operator"
    assert targets.peer_of("persona:operator") == "persona:architect"
    assert targets.peer_of("persona:casual") == "persona:operator"


def test_peer_of_unknown_raises() -> None:
    with pytest.raises(targets.UnknownTargetError):
        targets.peer_of("persona:bogus")
