from __future__ import annotations

import logging
from typing import Callable

from ubongo import events
from ubongo.config import load_config
from ubongo.llm import LLMError, complete
from ubongo.memory import store
from ubongo.memory.store import Message, Summary

logger = logging.getLogger("ubongo.memory.compaction")

Strategy = Callable[[str | None, list[Message]], str]

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
You maintain a running summary of an ongoing conversation. The summary is the durable memory the assistant relies on for facts older than the recall window.

Hard rules:
1. The existing summary (when one is provided) is a carry-forward. Every named entity, date, number, identifier, preference, "remember this" instruction, and concrete decision in it must appear in your output. You may rephrase, but you may not drop any of these.
2. When the new turns are repetitive or pattern-shaped (e.g., the same short reply repeated), summarize the pattern in ONE sentence at the end. Do not let the pattern overwrite earlier facts.
3. Always preserve concrete facts, names, dates, numbers, identifiers, preferences, and decisions stated by the user in the new turns too.
4. Drop banter and pleasantries.

Format: under 200 words, third person, plain prose, single paragraph or two short paragraphs at most."""


def default_strategy(prior_summary: str | None, messages: list[Message]) -> str:
    if not messages:
        return prior_summary or ""
    config = load_config()
    model = config.get("models", {}).get("compaction", "openrouter/anthropic/claude-haiku-4.5")
    transcript_lines = [f"{m.role}: {m.content}" for m in messages]
    transcript = "\n".join(transcript_lines)
    if prior_summary:
        user_content = (
            "## Existing summary (FACTS TO PRESERVE — every named entity, date, number, "
            "and 'remember this' from here must appear in your output)\n\n"
            f"{prior_summary}\n\n"
            "## New turns to fold in\n\n"
            f"{transcript}\n\n"
            "Write the updated summary now. Carry every fact above forward; integrate any "
            "new facts from the transcript; if the transcript is repetitive, describe the "
            "pattern in one sentence."
        )
    else:
        user_content = f"New turns to summarize:\n\n{transcript}"
    try:
        result = complete(
            system_prompt=_DEFAULT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
            model=model,
            max_tokens=400,
        )
        return result.text.strip()
    except LLMError as exc:
        logger.warning(
            "compaction_failed",
            extra={"strategy": "default", "cause": str(exc.cause) if exc.cause else None},
        )
        # On failure, prefer keeping the prior summary over losing it.
        if prior_summary:
            return prior_summary
        return f"[compaction failed: {len(messages)} messages were not summarized]"


register("default", default_strategy)


def _compaction_handler(payload: dict) -> None:
    """Subscribed to after_recall: triggers maybe_compact in-band."""
    conversation_id = payload.get("conversation_id")
    if conversation_id is None:
        return
    maybe_compact(conversation_id)


events.register("after_recall", _compaction_handler)


def maybe_compact(
    conversation_id: int,
    *,
    strategy: str = "default",
) -> Summary | None:
    """Run compaction if the message-since-summary count is past the threshold.

    Cumulative summaries: each compaction folds the previous summary text into
    the new one, so the latest summary always covers from message 1 forward.

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
    upper = max_id - recall_turns
    if upper <= floor_id:
        return None

    new_messages = store.messages_in_range(conversation_id, floor_id + 1, upper)
    if not new_messages:
        return None

    fn = get(strategy)
    summary_text = fn(last_summary.content if last_summary else None, new_messages)

    # Cumulative: the new summary covers from the very first message of the
    # conversation through the highest message id we just folded in.
    covers_from = (
        last_summary.covers_from_message_id if last_summary else new_messages[0].id
    )
    covers_to = new_messages[-1].id

    store.persist_summary(
        conversation_id=conversation_id,
        covers_from_message_id=covers_from,
        covers_to_message_id=covers_to,
        content=summary_text,
        strategy=strategy,
    )
    logger.info(
        "compaction_run",
        extra={
            "conversation_id": conversation_id,
            "strategy": strategy,
            "covers_from": covers_from,
            "covers_to": covers_to,
            "new_message_count": len(new_messages),
            "had_prior_summary": last_summary is not None,
        },
    )
    return store.latest_summary(conversation_id)
