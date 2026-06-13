from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import router  # noqa: E402
from ubongo.agents import personas  # noqa: E402
from ubongo.evolution import promotion, sandbox, targets  # noqa: E402
from ubongo.evaluation import CallBudget  # noqa: E402
from ubongo.memory import evolution_state
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


# --- router config_override + live swap -------------------------------------

def test_config_override_routing_restores(db) -> None:
    variant = {"rules": [{"match": {"intent": "technical"}, "workflow": "casual_reply"}],
               "default_workflow": "casual_reply"}
    from ubongo.classifier import Classification
    cls = Classification(intent="technical", tone="neutral", confidence=0.9,
                         risk="low", task_type=None, suggested_skill=None)
    base = router.route_workflow(cls)
    with router.config_override(routing=variant):
        assert router.route_workflow(cls) == "casual_reply"
    assert router.route_workflow(cls) == base  # restored


def test_live_swap_routing_promotion(db) -> None:
    variant_text = ("rules:\n- match: {intent: technical}\n  workflow: casual_reply\n"
                    "default_workflow: casual_reply")
    lid = evolution_state.append_lineage_variant(
        target="routing:default", parent_id=None, generation=1,
        variant_text=variant_text, variant_metadata={"strategy": "retarget", "kind": "config"})
    evolution_state.set_active_evolution("routing:default", lid)
    from ubongo.classifier import Classification
    cls = Classification(intent="technical", tone="neutral", confidence=0.9,
                         risk="low", task_type=None, suggested_skill=None)
    assert router.route_workflow(cls) == "casual_reply"  # promoted rule in effect
    evolution_state.clear_active_evolution("routing:default")
    assert router.route_workflow(cls) != "casual_reply"  # reverted


def test_live_swap_toolchain_promotion(db) -> None:
    tc = next(t for t in targets.evolvable_targets() if t.startswith("toolchain:"))
    wf = tc[len("toolchain:"):]
    variant_text = f"workflow: {wf}\nagents: [architect, evaluator]"
    lid = evolution_state.append_lineage_variant(
        target=tc, parent_id=None, generation=1, variant_text=variant_text,
        variant_metadata={"strategy": "add", "kind": "config"})
    evolution_state.set_active_evolution(tc, lid)
    assert router.workflow_agents(wf) == ("architect", "evaluator")
    evolution_state.clear_active_evolution(tc)
    assert router.workflow_agents(wf) != ("architect", "evaluator")


# --- evaluate_config_variant: isolated, no side effects ---------------------

@pytest.fixture
def stub_pipeline(monkeypatch):
    monkeypatch.setattr(sandbox, "_run_workflow_isolated", lambda agents, msg, *, persona: ("a response", 12))
    monkeypatch.setattr(sandbox, "_judge", lambda q, r, *, judge_model: (0.8, 0.1, False))
    from ubongo.classifier import Classification
    import ubongo.classifier as C
    monkeypatch.setattr(C, "classify", lambda m: Classification(
        intent="technical", tone="neutral", confidence=0.9, risk="low",
        task_type=None, suggested_skill=None))


_SAMPLES = {"version": "t", "conversations": [
    {"id": "a", "persona_affinity": None, "turns": [{"role": "user", "content": "q1?"}]},
    {"id": "b", "persona_affinity": None, "turns": [{"role": "user", "content": "q2?"}]},
]}


def test_evaluate_config_routing_produces_metrics(db, stub_pipeline) -> None:
    row = {"id": 1, "variant_text": "rules:\n- match: {intent: technical}\n  workflow: casual_reply\ndefault_workflow: casual_reply"}
    m = sandbox.evaluate_config_variant(row, "routing:default", _SAMPLES["conversations"],
                                        judge_model="j", budget=CallBudget(500))
    assert m is not None
    assert abs(m.success_rate - 0.8) < 1e-9
    assert m.lineage_id == 1


def test_evaluate_config_writes_no_side_effect_rows(db, stub_pipeline) -> None:
    row = {"id": 1, "variant_text": "rules:\n- match: {intent: technical}\n  workflow: casual_reply\ndefault_workflow: casual_reply"}
    sandbox.evaluate_config_variant(row, "routing:default", _SAMPLES["conversations"],
                                    judge_model="j", budget=CallBudget(500))
    conn = store.connection()
    assert conn.execute("SELECT COUNT(*) c FROM workflow_runs").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM agent_runs").fetchone()["c"] == 0


def test_evaluate_config_budget_all_or_nothing(db, stub_pipeline) -> None:
    row = {"id": 1, "variant_text": "rules:\n- match: {intent: technical}\n  workflow: casual_reply\ndefault_workflow: casual_reply"}
    # 2 samples * 5 calls = 10 needed; budget 4 -> skipped
    assert sandbox.evaluate_config_variant(row, "routing:default", _SAMPLES["conversations"],
                                           judge_model="j", budget=CallBudget(4)) is None


# v0.5 phase 05: retry:repair (and its structural-proxy evaluator) was cut
# (Amendment 2); the evaluator function no longer exists.
def test_retry_proxy_is_gone() -> None:
    assert not hasattr(sandbox, "evaluate_retry_variant")
