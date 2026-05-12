from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context, events, skills  # noqa: E402
from ubongo.agents.base import AgentInput  # noqa: E402
from ubongo.agents.research import ResearchAgent, _filter_messages_by_overlap, _tokens  # noqa: E402
from ubongo.llm import CompletionResult, LLMError  # noqa: E402
from ubongo.memory import store, vault  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")
    vault.set_vault_root(tmp_path / "vault")
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


def _completion(text: str = "findings here") -> CompletionResult:
    return CompletionResult(text=text, model="test-research", tokens_in=20, tokens_out=15, latency_ms=10, attempts=1)


def _seed_messages(persona: str = "architect") -> int:
    conv_id = store.current_or_new_conversation(persona)
    store.append_message(conv_id, "user", "we should add a caching layer for the API", persona=persona)
    store.append_message(conv_id, "assistant", "use a write-through cache with Redis", persona=persona)
    store.append_message(conv_id, "user", "what about pizza tonight", persona=persona)
    return conv_id


def test_tokens_excludes_stopwords_and_short_words():
    out = _tokens("How do I write a function to add numbers")
    assert "function" in out
    assert "numbers" in out
    assert "add" in out
    assert "how" not in out  # stopword
    assert "do" not in out  # stopword/short
    assert "to" not in out  # stopword


def test_filter_messages_by_overlap_keeps_only_matches():
    _seed_messages()
    all_recent = store.last_n_messages_global(30)
    hits = _filter_messages_by_overlap("explain caching strategy", all_recent)
    contents = [m.content for m in hits]
    assert any("caching" in c for c in contents)
    assert not any("pizza" in c for c in contents)


def test_search_daily_notes_returns_empty_when_dir_missing(tmp_path):
    vault.set_vault_root(tmp_path / "missing-vault")
    out = vault.search_daily_notes("caching")
    assert out == []


def test_search_daily_notes_returns_snippets_with_context(tmp_path):
    root = tmp_path / "vault"
    daily = root / "daily"
    daily.mkdir(parents=True)
    (daily / "2026-05-10.md").write_text(
        "---\ndate: 2026-05-10\n---\n# 2026-05-10\n\nWe talked about caching today and Redis.\n",
        encoding="utf-8",
    )
    (daily / "2026-05-11.md").write_text(
        "---\ndate: 2026-05-11\n---\n# 2026-05-11\n\nPizza for dinner. Tasty.\n",
        encoding="utf-8",
    )
    vault.set_vault_root(root)
    hits = vault.search_daily_notes("caching strategy")
    assert len(hits) == 1
    assert "caching" in hits[0].snippet.lower()
    assert hits[0].path.endswith("2026-05-10.md")


def test_research_run_happy_path_returns_ok_result():
    _seed_messages()
    agent = ResearchAgent()
    input = AgentInput(
        message="explain caching",
        history=({"role": "user", "content": "explain caching"},),
        summary_text=None,
        prior_findings=(),
    )
    with patch("ubongo.agents.research.complete", return_value=_completion("synthesis text")) as m:
        result = agent.run(input, context=None)
    assert result.ok is True
    assert result.text == "synthesis text"
    assert result.tokens_in == 20
    assert result.metadata["retrieved_messages"] >= 1
    m.assert_called_once()


def test_research_run_returns_ok_false_on_llm_error():
    _seed_messages()
    agent = ResearchAgent()
    input = AgentInput(
        message="explain caching",
        history=({"role": "user", "content": "explain caching"},),
        summary_text=None,
        prior_findings=(),
    )
    with patch(
        "ubongo.agents.research.complete",
        side_effect=LLMError("boom", cause=RuntimeError("nope")),
    ):
        result = agent.run(input, context=None)
    assert result.ok is False
    assert result.error == "research_llm_error"
    assert result.text == ""


def test_research_default_model_resolves_from_settings():
    agent = ResearchAgent()
    assert agent.default_model
    assert agent.max_tokens == 800


def test_last_n_messages_global_returns_cross_conversation():
    _seed_messages("architect")
    # Force a new conversation by calling start_conversation directly
    second = store.start_conversation("casual")
    store.append_message(second, "user", "different topic about gardening", persona="casual")
    out = store.last_n_messages_global(10)
    # should include messages from both conversations
    conv_ids = {m.conversation_id for m in out}
    assert len(conv_ids) >= 2
