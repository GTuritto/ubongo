from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Any

from ubongo import events, skills
from ubongo.config import load_config
from ubongo.llm import LLMError, complete

logger = logging.getLogger("ubongo.classifier")

INTENT_VOCAB = {"technical", "casual", "work", "research", "coding", "other"}
TONE_VOCAB = {"neutral", "frustrated", "excited", "tired", "curious"}
TASK_TYPE_VOCAB = {"command", "high_stakes_decision", "question", "chat", "none"}
RISK_VOCAB = {"low", "medium", "high", "destructive"}


@dataclass(frozen=True)
class Classification:
    intent: str
    tone: str
    task_type: str
    suggested_skill: str | None
    risk: str
    confidence: float


_FALLBACK = Classification(
    intent="other",
    tone="neutral",
    task_type="none",
    suggested_skill=None,
    risk="low",
    confidence=0.0,
)

_BASE_PROMPT = """\
You are a fast classifier. Read the user message and return ONLY a single JSON object with EXACTLY these keys:

- intent: one of [technical, casual, work, research, coding, other]. Definitions:
  - technical: software architecture, system/API design, engineering trade-offs, "how does X work", reliability/scaling patterns (e.g. "design a circuit breaker", "should I shard this table"). Design and reasoning, not writing code.
  - coding: write, debug, refactor, or explain a SPECIFIC piece of code (e.g. "write a function that reverses a list", "why does this loop crash").
  - research: gather or synthesize information about a topic from prior context or sources (e.g. "what did we decide about caching", "summarize the options").
  - work: concrete tasks, logistics, status, planning, ops actions (e.g. "rotate this API key", "what's left on the sprint").
  - casual: chit-chat, social, emotional, venting, small talk.
  - other: none of the above.
  When a message is a design/engineering question, prefer technical over work.
- tone: one of [neutral, frustrated, excited, tired, curious]
- task_type: one of [command, high_stakes_decision, question, chat, none]
- suggested_skill: {skill_clause}
- risk: one of [low, medium, high, destructive]
- confidence: a float between 0.0 and 1.0

No prose, no explanation, no code fences. Just the JSON object.\
"""


def _build_system_prompt() -> str:
    registered = skills.list_skills()
    if not registered:
        return _BASE_PROMPT.replace("{skill_clause}", "null")
    bullets = "\n".join(f"- {s.name} — {s.description}" for s in registered)
    skill_clause = (
        "one of the listed skill names below, or null if no skill applies"
    )
    return (
        _BASE_PROMPT.replace("{skill_clause}", skill_clause)
        + "\n\n## Available skills\n\n"
        + bullets
    )

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


def _validate(parsed: dict[str, Any]) -> Classification | None:
    try:
        intent = parsed["intent"]
        tone = parsed["tone"]
        task_type = parsed["task_type"]
        risk = parsed["risk"]
        suggested_skill = parsed.get("suggested_skill")
        confidence = parsed["confidence"]
    except (KeyError, TypeError):
        return None

    if intent not in INTENT_VOCAB:
        return None
    if tone not in TONE_VOCAB:
        return None
    if task_type not in TASK_TYPE_VOCAB:
        return None
    if risk not in RISK_VOCAB:
        return None
    if not isinstance(confidence, (int, float)):
        return None
    if suggested_skill is not None and not isinstance(suggested_skill, str):
        return None
    if isinstance(suggested_skill, str) and not skills.has(suggested_skill):
        logger.warning("classify_unknown_skill", extra={"skill_name": suggested_skill})
        suggested_skill = None

    confidence = max(0.0, min(1.0, float(confidence)))

    return Classification(
        intent=intent,
        tone=tone,
        task_type=task_type,
        suggested_skill=suggested_skill,
        risk=risk,
        confidence=confidence,
    )


def classify(message: str) -> Classification:
    events.dispatch("before_classify", {"message_length": len(message)})

    config = load_config()
    model = config["models"]["classifier"]

    try:
        result = complete(
            system_prompt=_build_system_prompt(),
            messages=[{"role": "user", "content": message}],
            model=model,
            max_tokens=128,
            temperature=0,  # a classifier must be stable across runs
        )
    except LLMError as exc:
        logger.warning(
            "classify_failed",
            extra={"reason": "llm_error", "cause": str(exc.cause) if exc.cause else None},
        )
        events.dispatch("after_classify", {"classification": asdict(_FALLBACK), "fallback": True})
        return _FALLBACK

    raw = _strip_fences(result.text or "")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("classify_failed", extra={"reason": "json_decode", "cause": str(exc), "raw_head": raw[:120]})
        events.dispatch("after_classify", {"classification": asdict(_FALLBACK), "fallback": True})
        return _FALLBACK

    if not isinstance(parsed, dict):
        logger.warning("classify_failed", extra={"reason": "not_an_object", "raw_head": raw[:120]})
        events.dispatch("after_classify", {"classification": asdict(_FALLBACK), "fallback": True})
        return _FALLBACK

    validated = _validate(parsed)
    if validated is None:
        logger.warning("classify_failed", extra={"reason": "schema_violation", "raw_head": raw[:120]})
        events.dispatch("after_classify", {"classification": asdict(_FALLBACK), "fallback": True})
        return _FALLBACK

    events.dispatch("after_classify", {"classification": asdict(validated), "fallback": False})
    return validated
