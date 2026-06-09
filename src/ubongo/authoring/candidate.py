"""The skill-candidate model and its LLM drafting (Phase 1a/1b).

`draft_candidate(description)` asks a strong model to author one skill from a
short capability description, returning a `SkillCandidate`. The model emits a
single fenced JSON object; parsing is defensive (fence-tolerant, field
defaulting) the same way the classifier/evaluator parsers are, because a draft
that fails to parse should degrade to a clear error, not a crash.

Drafting does NOT validate or persist — that is `validation.py` / `quarantine.py`.
This module only turns a description into a structured candidate. `complete` is
imported here (not injected) so tests patch `ubongo.authoring.candidate.complete`,
mirroring `evolution.generator`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from ubongo.config import load_authoring, load_config
from ubongo.llm import LLMError, complete

logger = logging.getLogger("ubongo.authoring.candidate")

_DEFAULT_MODEL = "openrouter/anthropic/claude-sonnet-4.5"
_MAX_TOKENS = 1500


class DraftError(Exception):
    """The model call failed or its output could not be parsed into a candidate."""


@dataclass(frozen=True)
class SkillCandidate:
    """One authored skill before validation/quarantine.

    `prompts` maps a prompt key to its full markdown body (not a path); the
    quarantine writer materializes each to `prompts/<key>.md` and records the
    `key -> prompts/<key>.md` mapping in SKILL.md frontmatter.

    `command_template` is an optional single constrained-bash command the skill
    is built around (e.g. ``git diff --stat``); None for a pure prompt skill.
    A non-empty command template makes this a "command skill", which validation
    forces to risk>=medium / irreversible.
    """

    name: str
    description: str
    risk: str
    reversibility: str
    default_persona: str | None
    body: str
    prompts: dict[str, str] = field(default_factory=dict)
    command_template: str | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def is_command_skill(self) -> bool:
        return bool(self.command_template and self.command_template.strip())

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "risk": self.risk,
            "reversibility": self.reversibility,
            "default_persona": self.default_persona,
            "body": self.body,
            "prompts": dict(self.prompts),
            "command_template": self.command_template,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SkillCandidate":
        return cls(
            name=str(data.get("name") or ""),
            description=str(data.get("description") or ""),
            risk=str(data.get("risk") or "low"),
            reversibility=str(data.get("reversibility") or "reversible"),
            default_persona=data.get("default_persona") or None,
            body=str(data.get("body") or ""),
            prompts={str(k): str(v) for k, v in (data.get("prompts") or {}).items()},
            command_template=(data.get("command_template") or None),
            metadata=dict(data.get("metadata") or {}),
        )


_SYSTEM_PROMPT = (
    "You author Ubongo skills. A skill is a reusable, progressive-disclosure unit "
    "of capability: a SKILL.md (frontmatter + a markdown body that instructs the "
    "model when it activates) plus optional prompt templates. Given a capability "
    "description, design ONE skill.\n\n"
    "Output a single JSON object, no prose, no code fence, with these fields:\n"
    "  name: kebab-case slug, lowercase letters/digits/hyphens only, <= 40 chars\n"
    "  description: one line, what the skill does and when to use it\n"
    "  risk: one of low | medium | high | destructive\n"
    "  reversibility: reversible | irreversible\n"
    "  default_persona: architect | operator | casual | null\n"
    "  body: the SKILL.md body (markdown instructions for the model)\n"
    "  prompts: object mapping a short key to a markdown prompt-template body "
    "(may be empty)\n"
    "  command_template: a SINGLE shell command the skill runs, or null for a "
    "pure-prompt skill. If present it MUST use only these programs: ls, pwd, "
    "echo, cat, head, tail, wc, grep, find, git, python, python3, pip, uv, "
    "pytest, sqlite3, true, false. No pipes, redirects, ';', '&&', backticks, or "
    "command substitution. No absolute paths outside the repo.\n\n"
    "Prefer the smallest skill that satisfies the description. Be honest about "
    "risk: anything that runs a command is at least medium and irreversible."
)


def _drafting_model() -> str:
    configured = (load_authoring() or {}).get("model")
    if configured:
        return str(configured)
    models = load_config().get("models", {})
    return models.get("coding") or models.get("default") or _DEFAULT_MODEL


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of a model response, tolerating a code fence or
    surrounding prose (mirror the evaluator's fence tolerance)."""
    raw = text.strip()
    if raw.startswith("```"):
        # drop the opening fence line (``` or ```json) and the closing fence
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    # If there is still surrounding prose, slice to the outermost braces.
    if not raw.startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise DraftError("model output contained no JSON object")
        raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DraftError(f"could not parse skill JSON: {exc}") from None
    if not isinstance(data, dict):
        raise DraftError("skill JSON was not an object")
    return data


def draft_candidate(description: str, *, source: str = "manual") -> SkillCandidate:
    """Draft one skill candidate from a capability description.

    Raises DraftError if the model call fails or its output cannot be parsed.
    The returned candidate is unvalidated and unpersisted.
    """
    if not description or not description.strip():
        raise DraftError("empty capability description")
    try:
        result = complete(
            system_prompt=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": description.strip()}],
            model=_drafting_model(),
            max_tokens=_MAX_TOKENS,
        )
    except LLMError as exc:
        logger.warning(
            "authoring_draft_failed",
            extra={"cause": str(exc.cause) if exc.cause else None},
        )
        raise DraftError(f"drafting model call failed: {exc}") from None

    data = _extract_json(result.text)
    data.setdefault("metadata", {})
    data["metadata"].update({"source": source, "description": description.strip()})
    candidate = SkillCandidate.from_dict(data)
    logger.info(
        "authoring_drafted",
        extra={"name": candidate.name, "command_skill": candidate.is_command_skill},
    )
    return candidate
