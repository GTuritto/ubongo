from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context, events, repl, skills  # noqa: E402
from ubongo.llm import CompletionResult, LLMError  # noqa: E402
from ubongo.memory import store, vault  # noqa: E402


def _completion(text: str) -> CompletionResult:
    return CompletionResult(text=text, model="test-model", tokens_in=10, tokens_out=10, latency_ms=5, attempts=1)


@pytest.fixture(autouse=True)
def _clean_env(tmp_path: Path):
    # isolated DB + vault for each test
    store.set_db_path(tmp_path / "ubongo.db")
    vault.set_vault_root(tmp_path / "vault")
    # point at real config/skills so summarize-conversation is registered
    skills.set_skills_dir(None)
    skills.reload()
    context.reload()
    events.clear()
    yield
    events.clear()
    skills.set_skills_dir(None)
    skills.reload()
    context.reload()
    store.set_db_path(None)
    vault.set_vault_root(None)


def _seed_conversation(persona: str = "casual") -> int:
    store.bootstrap()
    conv_id = store.current_or_new_conversation(persona)
    store.append_message(conv_id, "user", "tell me about my caching design", persona=persona)
    store.append_message(conv_id, "assistant", "We discussed LRU vs LFU.", persona=persona)
    store.append_message(conv_id, "user", "what about TTL?", persona=persona)
    store.append_message(conv_id, "assistant", "TTL adds a recency cap on top of either.", persona=persona)
    store.upsert_session(
        active_persona=persona,
        current_conversation_id=conv_id,
        last_message_at=store.now_iso(),
        auto_mode=False,
    )
    return conv_id


def test_summary_returns_empty_message_when_no_conversation() -> None:
    store.bootstrap()
    assert repl._run_summary() == "Not enough conversation yet to summarize."


def test_summary_calls_llm_with_operator_persona_and_skill() -> None:
    conv_id = _seed_conversation()
    captured: dict[str, object] = {}

    def fake_complete(system_prompt, messages, model, max_tokens):
        captured["system_prompt"] = system_prompt
        captured["messages"] = messages
        captured["model"] = model
        return _completion("Cache discussion: LRU vs LFU, plus TTL as a recency cap.")

    with patch("ubongo.repl.complete", side_effect=fake_complete):
        out = repl._run_summary()

    assert out == "Cache discussion: LRU vs LFU, plus TTL as a recency cap."
    # System prompt assembled with operator persona + skill body
    assert "You are the Operator persona of Ubongo." in captured["system_prompt"]
    assert "Active Skill: summarize-conversation" in captured["system_prompt"]
    # User message contains rendered transcript
    user_msg = captured["messages"][0]["content"]
    assert "Summarize the following conversation" in user_msg
    assert "User: tell me about my caching design" in user_msg
    assert "Ubongo: We discussed LRU vs LFU." in user_msg
    assert "{transcript}" not in user_msg


def test_summary_does_not_persist_anything() -> None:
    conv_id = _seed_conversation()
    before = store.last_n_messages(conv_id, 100)

    daily = vault.daily_note_path(__import__("datetime").date.today())
    pre_size = daily.stat().st_size if daily.exists() else 0

    after_sends: list[dict] = []
    events.register("after_send", after_sends.append)

    with patch("ubongo.repl.complete", return_value=_completion("Short recap.")):
        repl._run_summary()

    after = store.last_n_messages(conv_id, 100)
    assert len(after) == len(before)  # no new rows
    assert after_sends == []  # no after_send dispatched

    post_size = daily.stat().st_size if daily.exists() else 0
    assert post_size == pre_size  # vault untouched


def test_summary_short_circuits_on_llm_error() -> None:
    _seed_conversation()
    with patch("ubongo.repl.complete", side_effect=LLMError("boom", cause=RuntimeError("nope"))):
        out = repl._run_summary()
    assert "Sorry" in out


def test_render_skills_table_lists_registered() -> None:
    out = repl._render_skills_table()
    assert "summarize-conversation" in out
    assert "risk=low" in out
    assert "reversibility=reversible" in out


def test_reload_all_clears_caches() -> None:
    # warm caches
    skills.body("summarize-conversation")
    context.build_system_prompt("operator")
    msg = repl._reload_all()
    assert msg == "Reloaded UBONGO.md, personas, and skills."
    # caches cleared — registry needs re-discover
    assert skills._registry is None
    assert skills._body_cache == {}


def test_parse_skill_command_returns_name() -> None:
    assert repl._parse_skill_command("/skill summarize-conversation") == "summarize-conversation"
    assert repl._parse_skill_command("/skill  spaced-name  ") == "spaced-name"


def test_parse_skill_command_returns_none_when_no_arg() -> None:
    assert repl._parse_skill_command("/skill") is None
    assert repl._parse_skill_command("/skills") is None


def test_handle_text_uses_pending_skill(tmp_path: Path) -> None:
    _seed_conversation("operator")
    captured: dict[str, str] = {}

    def fake_complete(system_prompt, messages, model, max_tokens):
        captured["system_prompt"] = system_prompt
        return _completion("ok")

    with patch("ubongo.repl.complete", side_effect=fake_complete):
        text, ok, used_persona, skill_used, _token = repl.handle_text(
            "operator", "wrap this up please", auto_mode=False, pending_skill="summarize-conversation"
        )
    assert ok is True
    assert skill_used == "summarize-conversation"
    assert "Active Skill: summarize-conversation" in captured["system_prompt"]


def test_handle_text_ignores_unknown_pending_skill() -> None:
    _seed_conversation("operator")
    captured: dict[str, str] = {}

    def fake_complete(system_prompt, messages, model, max_tokens):
        captured["system_prompt"] = system_prompt
        return _completion("ok")

    with patch("ubongo.repl.complete", side_effect=fake_complete):
        _text, _ok, _used, skill_used, _token = repl.handle_text(
            "operator", "hi", auto_mode=False, pending_skill="phantom"
        )
    assert skill_used is None
    assert "Active Skill" not in captured["system_prompt"]
