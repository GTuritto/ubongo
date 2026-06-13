from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.authoring import gaps  # noqa: E402
from ubongo.memory import authoring_state
from ubongo.memory import store  # noqa: E402


@pytest.fixture
def db(tmp_path: Path):
    store.set_db_path(tmp_path / "t.db")
    store.bootstrap()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")


def _seed(intent: str, n: int, *, skill=None, confidence=0.9, content="do a thing"):
    conn = store.connection()
    conv = store.start_conversation("architect")
    for i in range(n):
        mid = store.append_message(conversation_id=conv, role="user", content=f"{content} {i}")
        cls = json.dumps({"intent": intent, "suggested_skill": skill, "confidence": confidence})
        conn.execute(
            "INSERT INTO workflow_runs (conversation_id, message_id, classification, workflow, "
            "execution_mode, started_at, outcome) VALUES (?,?,?,?,?,?, 'success')",
            (conv, mid, cls, "{}", "sequential", store.now_iso()),
        )


def test_next_gap_finds_recurring_unmet_intent(db) -> None:
    _seed("translation", 3, content="translate this to French")
    g = gaps.next_gap()
    assert g is not None
    assert g.intent == "translation"
    assert g.occurrences == 3
    assert "translation" in g.description
    assert "translate this to French" in g.description  # representative sample


def test_skill_matched_intent_is_not_a_gap(db) -> None:
    _seed("summary", 3, skill="summarize-conversation")
    assert gaps.next_gap() is None


def test_below_min_occurrences_is_not_a_gap(db) -> None:
    _seed("translation", 1)
    assert gaps.next_gap() is None


def test_worked_gap_excluded(db) -> None:
    _seed("translation", 3)
    rid = authoring_state.start_authoring_run(gap="translation")
    authoring_state.finish_authoring_run(rid, calls_spent=1, outcome="drafted")
    assert gaps.next_gap() is None


def test_aborted_gap_not_excluded(db) -> None:
    _seed("translation", 3)
    rid = authoring_state.start_authoring_run(gap="translation")
    authoring_state.finish_authoring_run(rid, calls_spent=1, outcome="aborted")
    # an aborted attempt should not block re-attempting the gap
    assert gaps.next_gap() is not None


def test_deterministic_frequency_order(db) -> None:
    _seed("translation", 2)
    _seed("scheduling", 4)
    g = gaps.next_gap()
    assert g.intent == "scheduling"  # higher count wins


def test_no_turns_returns_none(db) -> None:
    assert gaps.next_gap() is None
