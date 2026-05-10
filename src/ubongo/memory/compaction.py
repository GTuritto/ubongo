from __future__ import annotations

import logging
from typing import Callable

from ubongo.config import load_config
from ubongo.llm import LLMError, complete
from ubongo.memory import store
from ubongo.memory.store import Message, Summary

logger = logging.getLogger("ubongo.memory.compaction")

Strategy = Callable[[list[Message]], str]

_strategies: dict[str, Strategy] = {}


def register(name: str, strategy: Strategy) -> None:
    _strategies[name] = strategy


def get(name: str) -> Strategy:
    if name not in _strategies:
        raise KeyError(f"Unknown compaction strategy: {name}")
    return _strategies[name]


def list_strategies() -> list[str]:
    return sorted(_strategies.keys())


_DEFAULT_SYSTEM_PROMPT = """\
You are a tight summarizer. Read the conversation excerpt and produce a single \
factual paragraph that captures what was discussed and decided. No preamble, no list, \
no closing remarks. Under 120 words. Third person. Plain prose."""


def default_strategy(messages: list[Message]) -> str:
    if not messages:
        return ""
    config = load_config()
    model = config.get("models", {}).get("compaction", "openrouter/anthropic/claude-haiku-4.5")
    transcript_lines = [f"{m.role}: {m.content}" for m in messages]
    transcript = "\n".join(transcript_lines)
    try:
        result = complete(
            system_prompt=_DEFAULT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": transcript}],
            model=model,
            max_tokens=256,
        )
        return result.text.strip()
    except LLMError as exc:
        logger.warning(
            "compaction_failed",
            extra={"strategy": "default", "cause": str(exc.cause) if exc.cause else None},
        )
        return f"[compaction failed: {len(messages)} messages were not summarized]"


register("default", default_strategy)


def maybe_compact(
    conversation_id: int,
    *,
    strategy: str = "default",
) -> Summary | None:
    """Run compaction if the message-since-summary count is past the threshold.

    Returns the persisted Summary or None if no compaction was needed.
    """
    config = load_config()
    memory_cfg = config.get("memory", {}) or {}
    recall_turns = int(memory_cfg.get("recall_turns", 10))
    trigger_at = int(memory_cfg.get("compaction", {}).get("trigger_at_turns", 30))

    since = store.count_messages_since_summary(conversation_id)
    if since < trigger_at:
        return None

    last_summary = store.latest_summary(conversation_id)
    floor_id = last_summary.covers_to_message_id if last_summary else 0
    max_id = store.max_message_id(conversation_id)
    # Summarize messages from (floor_id + 1) up to (max_id - recall_turns).
    upper = max_id - recall_turns
    if upper <= floor_id:
        return None

    messages = store.messages_in_range(conversation_id, floor_id + 1, upper)
    if not messages:
        return None

    fn = get(strategy)
    summary_text = fn(messages)
    summary_id = store.persist_summary(
        conversation_id=conversation_id,
        covers_from_message_id=messages[0].id,
        covers_to_message_id=messages[-1].id,
        content=summary_text,
        strategy=strategy,
    )
    logger.info(
        "compaction_run",
        extra={
            "conversation_id": conversation_id,
            "strategy": strategy,
            "covers_from": messages[0].id,
            "covers_to": messages[-1].id,
            "message_count": len(messages),
        },
    )
    return store.latest_summary(conversation_id)
