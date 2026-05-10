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
    compaction.register("stub", lambda prior, msgs: "STUB")
    assert compaction.get("stub")(None, []) == "STUB"


def test_maybe_compact_below_threshold_returns_none(db) -> None:
    cid = store.start_conversation("architect")
    for _ in range(10):
        store.append_message(cid, "user", "x")
    # 10 < 15 trigger; should not run.
    assert compaction.maybe_compact(cid) is None


def test_maybe_compact_at_threshold_persists_summary(db, stub_strategy_kept) -> None:
    compaction.register("stub", lambda prior, msgs: f"STUB:{len(msgs)}")
    cid = store.start_conversation("architect")
    for i in range(15):
        store.append_message(cid, "user", f"q{i}")
    summary = compaction.maybe_compact(cid, strategy="stub")
    assert summary is not None
    # 15 messages, recall_turns=10; summary covers ids 1..(15-10)=5.
    assert summary.covers_from_message_id == 1
    assert summary.covers_to_message_id == 5
    assert summary.strategy == "stub"
    assert summary.content == "STUB:5"


def test_cumulative_summary_folds_prior_into_new(db, stub_strategy_kept) -> None:
    """After two compactions, the latest summary covers from message 1
    (cumulative), not just the recent slice. This is the bugfix that prevents
    facts mentioned early in a long conversation from being orphaned."""
    captured: list[tuple[str | None, list]] = []

    def stub(prior: str | None, msgs: list) -> str:
        captured.append((prior, list(msgs)))
        ids = [m.id for m in msgs]
        return f"after-{prior or 'none'}:{ids[0]}-{ids[-1]}"

    compaction.register("stub", stub)
    cid = store.start_conversation("architect")
    for i in range(15):
        store.append_message(cid, "user", f"q{i}")
    first = compaction.maybe_compact(cid, strategy="stub")
    assert first is not None
    assert first.covers_from_message_id == 1
    assert first.covers_to_message_id == 5
    assert first.content == "after-none:1-5"

    # Add 15 more messages; should trigger another compaction (15 since last summary).
    for i in range(15):
        store.append_message(cid, "user", f"q{i+15}")
    second = compaction.maybe_compact(cid, strategy="stub")
    assert second is not None
    # Cumulative coverage: from id 1 through (max - recall_turns) = 30 - 10 = 20.
    assert second.covers_from_message_id == 1
    assert second.covers_to_message_id == 20
    # The strategy received the prior summary as its first arg.
    last_call = captured[-1]
    assert last_call[0] == "after-none:1-5"
    new_msg_ids = [m.id for m in last_call[1]]
    assert new_msg_ids == list(range(6, 21))


def test_maybe_compact_idempotent_when_no_new_messages_warrant_it(db, stub_strategy_kept) -> None:
    compaction.register("stub", lambda prior, msgs: f"STUB:{len(msgs)}")
    cid = store.start_conversation("architect")
    for i in range(15):
        store.append_message(cid, "user", f"q{i}")
    first = compaction.maybe_compact(cid, strategy="stub")
    assert first is not None
    # Add 5 more messages; total 20. Messages since last summary = 20 - 5 = 15... actually trigger fires.
    # To check idempotency we must add few enough that count_since_summary < trigger.
    for i in range(5):
        store.append_message(cid, "user", f"more{i}")
    # 20 messages; covers_to = 5; since = 15 -> at trigger threshold so it fires once more.
    second = compaction.maybe_compact(cid, strategy="stub")
    assert second is not None  # this one runs
    # Now since=0; another call should be a no-op.
    third = compaction.maybe_compact(cid, strategy="stub")
    assert third is None


def test_default_strategy_calls_compaction_model(db) -> None:
    cid = store.start_conversation("architect")
    for i in range(15):
        store.append_message(cid, "user", f"q{i}")
    with patch("ubongo.memory.compaction.complete", return_value=_completion("a tight summary")) as mock_complete:
        summary = compaction.maybe_compact(cid)  # default strategy
    assert summary is not None
    assert summary.content == "a tight summary"
    args, kwargs = mock_complete.call_args
    # Compaction uses the haiku-class compaction model from settings.yaml
    assert "haiku" in kwargs["model"]


def test_default_strategy_includes_prior_summary_in_prompt(db) -> None:
    cid = store.start_conversation("architect")
    # Pre-seed a prior summary
    store.append_message(cid, "user", "old")
    store.persist_summary(cid, 1, 1, "User said their birthday is March 15.", "default")
    # Add enough new messages to trigger another compaction.
    for i in range(15):
        store.append_message(cid, "user", f"q{i}")
    with patch("ubongo.memory.compaction.complete", return_value=_completion("updated summary")) as mock_complete:
        compaction.maybe_compact(cid)
    args, kwargs = mock_complete.call_args
    user_content = kwargs["messages"][0]["content"]
    assert "User said their birthday is March 15." in user_content
    assert "Existing summary" in user_content
