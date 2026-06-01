from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import events  # noqa: E402
from ubongo.agents import personas  # noqa: E402
from ubongo.evolution import generator, loop, sandbox, selection, targets  # noqa: E402
from ubongo.evolution.sandbox import CallBudget  # noqa: E402
from ubongo.memory import store  # noqa: E402


class _Fake:
    def __init__(self, text):
        self.text = text
        self.model = "fake"
        self.tokens_in = self.tokens_out = self.latency_ms = self.attempts = 1


def _fake_complete(system_prompt, messages, model, max_tokens):
    if "evaluation judge" in system_prompt.lower():
        return _Fake(json.dumps({"quality": 0.8, "hallucination": 0.1, "would_user_correct": False}))
    return _Fake("a variant prompt body")


@pytest.fixture
def db(tmp_path: Path, monkeypatch):
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    personas.reload()
    events.clear()
    monkeypatch.setattr(generator, "complete", _fake_complete)
    monkeypatch.setattr(sandbox, "complete", _fake_complete)
    yield
    events.clear()
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")


def test_cycle1_generates_from_base(db) -> None:
    r = loop.run_one_cycle(budget=CallBudget(200))
    assert r.action == "generated"
    assert r.generation == 1
    assert r.evaluated > 0
    rows = store.lineage_for_target(r.target, generation=1)
    assert rows and all(row["parent_id"] is None for row in rows)
    # an evolution_runs row was recorded
    assert store.evolution_runs_recent(1)[0]["outcome"] in ("completed", "partial")


def test_cycle_round_robins_targets(db) -> None:
    seen = []
    for _ in range(3):
        seen.append(loop.run_one_cycle(budget=CallBudget(200)).target)
    assert sorted(seen) == sorted(targets.evolvable_targets())  # each hit once


def test_cycle2_seeds_from_survivor(db, monkeypatch) -> None:
    # Pin the target so two cycles both hit it: gen1 from base, gen2 from champion.
    monkeypatch.setattr(selection, "next_target", lambda: "persona:architect")
    loop.run_one_cycle(budget=CallBudget(200))
    r2 = loop.run_one_cycle(budget=CallBudget(200))
    assert r2.generation == 2
    gen2 = store.lineage_for_target("persona:architect", generation=2)
    parents = {row["parent_id"] for row in gen2}
    assert parents and None not in parents  # cross-generation lineage


def test_cycle_respects_budget(db, monkeypatch) -> None:
    monkeypatch.setattr(selection, "next_target", lambda: "persona:architect")
    r = loop.run_one_cycle(budget=CallBudget(4))
    # Generation gated to <=4 LLM calls; evaluation then gets nothing.
    assert r.calls_spent <= 4
    assert r.evaluated == 0


def test_cycle_emits_event(db) -> None:
    captured = []
    events.register("evolution_generation", lambda p: captured.append(p))
    loop.run_one_cycle(budget=CallBudget(200))
    assert captured and captured[0]["action"] == "generated"


def test_idle_when_no_targets(db, monkeypatch) -> None:
    monkeypatch.setattr(targets, "evolvable_targets", lambda: [])
    r = loop.run_one_cycle(budget=CallBudget(200))
    assert r.action == "idle"
    assert r.target is None
