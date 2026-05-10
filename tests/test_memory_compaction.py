from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.llm import CompletionResult  # noqa: E402
from ubongo.memory import compaction, store  # noqa: E402


def _completion(text: str) -> CompletionResult:
    return CompletionResult(text=text, model="m", tokens_in=1, tokens_out=1, latency_ms=1, attempts=1)


@pytest.fixture
def db(tmp_path: Path):
    store.set_db_path(tmp_path / "test.db")
    store.bootstrap()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")


@pytest.fixture
def stub_strategy_kept():
    yield
    # Keep the default registered after each test; remove ad-hoc ones we add.
    for name in list(compaction.list_strategies()):
        if name != "default":
            del compaction._strategies[name]


def test_default_strategy_registered_at_import() -> None:
    assert "default" in compaction.list_strategies()


def test_get_unknown_strategy_raises(stub_strategy_kept) -> None:
    with pytest.raises(KeyError):
        compaction.get("nope")


def test_register_and_get_custom_strategy(stub_strategy_kept) -> None:
    compaction.register("stub", lambda msgs: "STUB")
    assert compaction.get("stub")([]) == "STUB"


def test_maybe_compact_below_threshold_returns_none(db) -> None:
    cid = store.start_conversation("architect")
    for _ in range(10):
        store.append_message(cid, "user", "x")
    # 10 < 30; should not trigger.
    assert compaction.maybe_compact(cid) is None


def test_maybe_compact_at_threshold_persists_summary(db, stub_strategy_kept) -> None:
    compaction.register("stub", lambda msgs: f"STUB:{len(msgs)}")
    cid = store.start_conversation("architect")
    for i in range(31):
        store.append_message(cid, "user", f"q{i}")
    summary = compaction.maybe_compact(cid, strategy="stub")
    assert summary is not None
    # 31 messages; recall_turns=10; summary covers ids 1..(31-10)=21.
    assert summary.covers_from_message_id == 1
    assert summary.covers_to_message_id == 21
    assert summary.strategy == "stub"
    assert summary.content == "STUB:21"


def test_maybe_compact_idempotent_when_no_new_messages_warrant_it(db, stub_strategy_kept) -> None:
    compaction.register("stub", lambda msgs: f"STUB:{len(msgs)}")
    cid = store.start_conversation("architect")
    for i in range(31):
        store.append_message(cid, "user", f"q{i}")
    first = compaction.maybe_compact(cid, strategy="stub")
    assert first is not None
    # Add 5 more messages; total 36. Messages since last summary = 36 - 21 = 15 < 30.
    for i in range(5):
        store.append_message(cid, "user", f"more{i}")
    again = compaction.maybe_compact(cid, strategy="stub")
    assert again is None
    # Only one summary row exists for this conversation.
    conn = store.connection()
    count = conn.execute("SELECT COUNT(*) AS c FROM summaries WHERE conversation_id = ?", (cid,)).fetchone()
    assert count["c"] == 1


def test_default_strategy_calls_compaction_model(db) -> None:
    cid = store.start_conversation("architect")
    for i in range(31):
        store.append_message(cid, "user", f"q{i}")
    with patch("ubongo.memory.compaction.complete", return_value=_completion("a tight summary")) as mock_complete:
        summary = compaction.maybe_compact(cid)  # default strategy
    assert summary is not None
    assert summary.content == "a tight summary"
    args, kwargs = mock_complete.call_args
    # Compaction uses the haiku-class compaction model from settings.yaml
    assert "haiku" in kwargs["model"]
