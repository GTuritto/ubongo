from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context  # noqa: E402
from ubongo.agents import personas  # noqa: E402
from ubongo.evolution import promotion  # noqa: E402
from ubongo.memory import evolution_state
from ubongo.memory import store, vault  # noqa: E402


@pytest.fixture
def db(tmp_path: Path):
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    vault.set_vault_root(tmp_path / "vault")
    personas.reload()
    context.reload()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")
    vault.set_vault_root(None)
    context.reload()


_MARKER = "PROMOTED_ARCHITECT_BODY_MARKER_12345"


def _seed_and_propose() -> int:
    lid = evolution_state.append_lineage_variant(
        target="persona:architect", parent_id=None, generation=1,
        variant_text=_MARKER, variant_metadata={"strategy": "prune", "kind": "prompt"},
    )
    evolution_state.append_evaluation(
        lineage_id=lid, sample_set="s", success_rate=0.9, cost=1, latency_ms=1,
        hallucination_rate=0, user_correction_rate=0, fitness=0.9,
    )
    return promotion.propose_if_better("persona:architect", 1)


def test_persona_prompt_uses_file_body_before_promotion(db) -> None:
    _seed_and_propose()
    assert _MARKER not in context.build_system_prompt("architect")


def test_persona_prompt_swaps_after_approve(db) -> None:
    pid = _seed_and_propose()
    promotion.approve(pid)
    prompt = context.build_system_prompt("architect")
    assert _MARKER in prompt  # live swap: promoted body is now used
    # frontmatter still comes from the file -> the persona still loads a model
    assert personas.get("architect").model


def test_rollback_reverts_to_file_body(db) -> None:
    pid = _seed_and_propose()
    promotion.approve(pid)
    assert _MARKER in context.build_system_prompt("architect")
    promotion.rollback("persona:architect")
    assert _MARKER not in context.build_system_prompt("architect")


def test_build_system_prompt_no_db_uses_file(tmp_path, monkeypatch) -> None:
    # A process with no DB connection must not bootstrap one for prompt assembly.
    monkeypatch.setattr(store, "_connection", None)
    monkeypatch.setattr(store, "is_connected", lambda: False)
    # Should not raise and should return the file body.
    out = context.build_system_prompt("architect")
    assert isinstance(out, str) and out.strip()
