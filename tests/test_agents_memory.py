from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context, events, skills  # noqa: E402
from ubongo.agents.base import AgentInput  # noqa: E402
from ubongo.agents.memory import (  # noqa: E402
    MemoryAgent,
    assert_memory_writer,
    default_memory_agent,
    memory_writer,
)
from ubongo.memory import store, vault  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")
    vault.set_vault_root(tmp_path / "vault")
    skills.set_skills_dir(None)
    skills.reload()
    context.reload()
    events.clear()
    events.register("after_send", default_memory_agent.project_vault)
    yield
    events.clear()
    events.register("after_send", default_memory_agent.project_vault)
    skills.set_skills_dir(None)
    skills.reload()
    context.reload()
    store.set_db_path(None)
    vault.set_vault_root(None)


@pytest.fixture
def strict_memory_writer(monkeypatch):
    """Wraps store.append_message and vault.append_to_daily_note with the
    single-writer assertion so violations raise. Production code paths that
    flow through MemoryAgent pass; direct callers do not."""
    real_append_message = store.append_message
    real_vault_append = vault.append_to_daily_note

    def guarded_append_message(conversation_id, role, content, **kwargs):
        if role == "assistant":
            assert_memory_writer()
        return real_append_message(conversation_id, role, content, **kwargs)

    def guarded_vault_append(*args, **kwargs):
        assert_memory_writer()
        return real_vault_append(*args, **kwargs)

    monkeypatch.setattr(store, "append_message", guarded_append_message)
    monkeypatch.setattr(vault, "append_to_daily_note", guarded_vault_append)
    yield


def test_commit_assistant_turn_writes_message_with_persona_and_tokens():
    conv_id = store.current_or_new_conversation("casual")
    agent = MemoryAgent()
    msg_id = agent.commit_assistant_turn(
        conversation_id=conv_id,
        content="hello back",
        persona="casual",
        model="m",
        tokens_in=3,
        tokens_out=4,
    )
    assert msg_id > 0
    messages = store.last_n_messages(conv_id, 5)
    last = messages[-1]
    assert last.role == "assistant"
    assert last.content == "hello back"
    assert last.persona == "casual"
    assert last.tokens_in == 3
    assert last.tokens_out == 4


def test_run_writes_assistant_message_via_metadata():
    conv_id = store.current_or_new_conversation("architect")
    agent = MemoryAgent()
    input = AgentInput(
        message="anything",
        history=(),
        summary_text=None,
        prior_findings=(),
        metadata={
            "conversation_id": conv_id,
            "response_text": "the answer",
            "persona": "architect",
            "model": "m",
            "tokens_in": 5,
            "tokens_out": 7,
        },
    )
    result = agent.run(input, context=None)
    assert result.ok is True
    assert result.metadata["assistant_message_id"] > 0
    msgs = store.last_n_messages(conv_id, 5)
    assert msgs[-1].content == "the answer"


def test_run_returns_failure_when_metadata_missing():
    agent = MemoryAgent()
    input = AgentInput(
        message="anything", history=(), summary_text=None, prior_findings=(),
        metadata={},
    )
    result = agent.run(input, context=None)
    assert result.ok is False
    assert result.error == "memory_missing_input"


def test_after_send_vault_projection_runs_under_writer_token(tmp_path):
    conv_id = store.current_or_new_conversation("casual")
    payload = {
        "user_message": "hi",
        "response": "back at you",
        "persona": "casual",
        "auto_routed": False,
        "ts": "2026-05-12T10:00:00.000Z",
        "conversation_id": conv_id,
    }
    # Going through the registered MemoryAgent handler should land a vault file.
    events.dispatch("after_send", payload)
    daily_dir = vault._vault_root() / vault._daily_subdir()
    files = list(daily_dir.glob("*.md"))
    assert len(files) == 1
    assert "back at you" in files[0].read_text(encoding="utf-8")


def test_strict_mode_blocks_non_memory_writer(strict_memory_writer):
    """A synthetic caller (e.g. a future Coding Agent) writing the assistant
    message directly without entering memory_writer() must raise."""
    conv_id = store.current_or_new_conversation("operator")
    with pytest.raises(RuntimeError, match="single-writer rule"):
        store.append_message(conv_id, "assistant", "rogue write", persona="operator")


def test_strict_mode_allows_memory_agent_path(strict_memory_writer):
    """MemoryAgent.commit_assistant_turn holds the token; the same call from
    the agent must NOT raise under the strict-mode fixture."""
    conv_id = store.current_or_new_conversation("operator")
    agent = MemoryAgent()
    msg_id = agent.commit_assistant_turn(
        conversation_id=conv_id,
        content="legitimate write",
        persona="operator",
        model="m",
        tokens_in=0,
        tokens_out=0,
    )
    assert msg_id > 0


def test_assert_memory_writer_is_noop_inside_writer_block():
    with memory_writer():
        assert_memory_writer()  # must not raise


def test_assert_memory_writer_raises_outside_writer_block():
    with pytest.raises(RuntimeError, match="single-writer rule"):
        assert_memory_writer()
