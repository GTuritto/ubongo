from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import router  # noqa: E402
from ubongo.agents import personas  # noqa: E402
from ubongo.evolution import generator, targets  # noqa: E402
from ubongo.memory import store  # noqa: E402


@pytest.fixture
def db(tmp_path: Path):
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    personas.reload()
    router.reload()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")
    router.reload()


def _a_toolchain() -> str:
    return next(t for t in targets.evolvable_targets() if t.startswith("toolchain:"))


# --- serialize + resolve_base -----------------------------------------------

def test_serialize_routing_round_trips(db) -> None:
    text = targets.serialize_config("routing:default")
    parsed = yaml.safe_load(text)
    assert "rules" in parsed and "default_workflow" in parsed


def test_resolve_base_config_is_serialized_live(db) -> None:
    assert targets.resolve_base("routing:default") == targets.serialize_config("routing:default")


def test_config_targets_have_no_peer(db) -> None:
    assert targets.peer_of("routing:default") is None


# --- apply_variant validation -----------------------------------------------

def test_routing_valid_round_trips(db) -> None:
    base = targets.serialize_config("routing:default")
    assert isinstance(targets.apply_variant("routing:default", base), dict)


def test_routing_rejects_unknown_workflow(db) -> None:
    bad = "rules:\n- match: {intent: technical}\n  workflow: nope\ndefault_workflow: casual_reply"
    with pytest.raises(targets.InvalidVariantError):
        targets.apply_variant("routing:default", bad)


def test_routing_rejects_bad_yaml(db) -> None:
    with pytest.raises(targets.InvalidVariantError):
        targets.apply_variant("routing:default", "{not: valid: yaml:")


def test_toolchain_rejects_unknown_agent(db) -> None:
    tc = _a_toolchain()
    with pytest.raises(targets.InvalidVariantError):
        targets.apply_variant(tc, "workflow: x\nagents: [architect, ghost]")


def test_toolchain_rejects_no_composer(db) -> None:
    tc = _a_toolchain()
    with pytest.raises(targets.InvalidVariantError):
        targets.apply_variant(tc, "workflow: x\nagents: [evaluator]")


# v0.5 phase 05: retry:repair was cut (Amendment 2) — apply_variant now rejects it
# as an unknown target.
def test_retry_target_is_gone(db) -> None:
    assert not targets.is_target("retry:repair")
    with pytest.raises(targets.UnknownTargetError):
        targets.apply_variant("retry:repair", "max_attempts: 2")


# --- config generation ------------------------------------------------------

def test_routing_generation_all_valid(db) -> None:
    vs = generator.generate("routing:default", 4)
    assert vs
    for v in vs:
        assert v.metadata["kind"] == "config"
        targets.apply_variant("routing:default", v.text)  # must validate


def test_toolchain_generation_all_valid(db) -> None:
    tc = _a_toolchain()
    vs = generator.generate(tc, 4)
    assert vs
    for v in vs:
        targets.apply_variant(tc, v.text)


def test_config_variants_differ_from_base(db) -> None:
    base = targets.serialize_config("routing:default").strip()
    for v in generator.generate("routing:default", 4):
        assert v.text.strip() != base
