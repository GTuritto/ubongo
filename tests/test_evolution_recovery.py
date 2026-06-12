from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.agents import personas  # noqa: E402
from ubongo.evolution import generator, loop, sandbox, selection  # noqa: E402
from ubongo.evolution.sandbox import CallBudget  # noqa: E402
from ubongo.memory import evolution_state
from ubongo.memory import store  # noqa: E402


class _Fake:
    def __init__(self, text):
        self.text = text
        self.model = "fake"
        self.tokens_in = self.tokens_out = self.latency_ms = self.attempts = 1


def _fake_complete(system_prompt, messages, model, max_tokens):
    if "evaluation judge" in system_prompt.lower():
        return _Fake(json.dumps({"quality": 0.7, "hallucination": 0.1, "would_user_correct": False}))
    return _Fake("a variant body")


@pytest.fixture
def db(tmp_path: Path, monkeypatch):
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    personas.reload()
    monkeypatch.setattr(generator, "complete", _fake_complete)
    monkeypatch.setattr(sandbox, "complete", _fake_complete)
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")


def test_unevaluated_generation_is_reevaluated_not_regenerated(db, monkeypatch) -> None:
    monkeypatch.setattr(selection, "next_target", lambda: "persona:architect")
    # Simulate a crash mid-evaluation: a generation with variants but no evals.
    for i in range(3):
        evolution_state.append_lineage_variant(
            target="persona:architect", parent_id=None, generation=1,
            variant_text=f"v{i}", variant_metadata={"strategy": "paraphrase"},
        )
    assert evolution_state.evaluations_for_target("persona:architect", generation=1) == []

    r = loop.run_one_cycle(budget=CallBudget(200))
    # Resumes the SAME generation, does not create generation 2.
    assert r.action == "reevaluated"
    assert r.generation == 1
    assert evolution_state.max_lineage_generation("persona:architect") == 1
    assert len(evolution_state.evaluations_for_target("persona:architect", generation=1)) == 3


def test_interrupted_runs_are_listed(db) -> None:
    rid = evolution_state.start_evolution_run(target="persona:casual", generation=1)
    assert [r["id"] for r in evolution_state.interrupted_evolution_runs()] == [rid]
    evolution_state.finish_evolution_run(rid, calls_spent=5, outcome="completed")
    assert evolution_state.interrupted_evolution_runs() == []
