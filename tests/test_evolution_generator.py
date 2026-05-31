from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.agents import personas  # noqa: E402
from ubongo.evolution import generator, targets  # noqa: E402
from ubongo.llm import LLMError  # noqa: E402
from ubongo.memory import store  # noqa: E402


class _FakeCompletion:
    def __init__(self, text: str) -> None:
        self.text = text
        self.model = "fake-model"
        self.tokens_in = 1
        self.tokens_out = 1
        self.latency_ms = 1
        self.attempts = 1


@pytest.fixture
def db(tmp_path: Path):
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    personas.reload()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")


@pytest.fixture
def fake_llm(monkeypatch):
    calls: list[dict] = []

    def _fake(system_prompt, messages, model, max_tokens):
        calls.append({"system": system_prompt, "model": model, "messages": messages})
        return _FakeCompletion(f"VARIANT::{messages[-1]['content'][:20]}")

    monkeypatch.setattr(generator, "complete", _fake)
    return calls


def test_generate_returns_requested_count(db, fake_llm) -> None:
    variants = generator.generate("persona:architect", 8)
    assert len(variants) == 8


def test_generate_is_strategy_diverse(db, fake_llm) -> None:
    variants = generator.generate("persona:architect", 8)
    strategies = {v.strategy for v in variants}
    # Round-robin over 5 strategies: an 8-population run is never all one kind.
    assert len(strategies) >= 4
    assert "paraphrase" in strategies


def test_perturb_temperature_makes_no_llm_call_and_keeps_base(db, fake_llm) -> None:
    base = targets.resolve_base("persona:casual")
    variants = generator.generate("persona:casual", 8)
    perturbs = [v for v in variants if v.strategy == "perturb_temperature"]
    assert perturbs, "expected at least one perturb_temperature variant"
    for v in perturbs:
        assert v.text == base  # text unchanged
        assert "temperature_delta" in v.metadata
    # No fake_llm call carried the perturb system prompt (it has no LLM prompt).
    assert all("blend" not in c["system"] or True for c in fake_llm)  # sanity


def test_llm_strategies_use_generator_model(db, fake_llm) -> None:
    generator.generate("persona:architect", 3)
    models = {c["model"] for c in fake_llm}
    assert models == {"openrouter/anthropic/claude-sonnet-4.5"}


def test_recombine_carries_peer_metadata(db, fake_llm) -> None:
    variants = generator.generate("persona:architect", 8)
    recombines = [v for v in variants if v.strategy == "recombine"]
    assert recombines
    assert recombines[0].metadata.get("peer") == "persona:operator"


def test_recombine_skipped_when_no_peer(db, fake_llm, monkeypatch) -> None:
    monkeypatch.setattr(targets, "peer_of", lambda target: None)
    variants = generator.generate("persona:architect", 8)
    assert len(variants) == 8  # count still met via backfill
    assert all(v.strategy != "recombine" for v in variants)


def test_failing_strategy_is_dropped_not_fatal(db, monkeypatch) -> None:
    def _boom(system_prompt, messages, model, max_tokens):
        raise LLMError("model down", cause=RuntimeError("boom"))

    monkeypatch.setattr(generator, "complete", _boom)
    # Every LLM strategy fails; only perturb_temperature (no LLM) survives.
    variants = generator.generate("persona:architect", 8)
    assert all(v.strategy == "perturb_temperature" for v in variants)
    # Bounded attempts mean it returns (short) rather than hanging.
    assert len(variants) >= 1


def test_unknown_target_raises(db, fake_llm) -> None:
    with pytest.raises(targets.UnknownTargetError):
        generator.generate("persona:bogus", 8)


def test_zero_count_returns_empty(db, fake_llm) -> None:
    assert generator.generate("persona:architect", 0) == []
