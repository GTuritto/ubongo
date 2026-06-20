"""Verbosity per domain (v0.5 phase 07): a legible, manual-first length knob.

Verbosity is governance config, not a learned behavior: `governance.yaml` maps a
domain (the classifier's `task_type`, with `intent` as a fallback) to a level
(`terse | normal | deep`), with a default. `level_for` resolves the level for a
turn; `directive_text` turns it into the one prompt line the persona appends.
`normal` is a deliberate no-op — it keeps the persona's natural length, so the
knob only ever shortens or lengthens on an explicit mapping.

Learned legibility (a GP-evolvable `verbosity:<domain>` target) is deferred
(ADR-0022); promotion would stay human-approved like every other target, so the
boundary cannot move itself. This module reads config and writes nothing.
"""

from __future__ import annotations

from ubongo.config import load_governance

LEVELS = ("terse", "normal", "deep")
_DEFAULT = "normal"


def _block() -> dict:
    block = load_governance().get("verbosity", {}) or {}
    return block if isinstance(block, dict) else {}


def default_level() -> str:
    lvl = str(_block().get("default", _DEFAULT)).lower()
    return lvl if lvl in LEVELS else _DEFAULT


def levels_map() -> dict[str, str]:
    """The configured domain -> level map (validated to known levels)."""
    raw = _block().get("levels", {}) or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v).lower() for k, v in raw.items() if str(v).lower() in LEVELS}


def level_for(classification) -> str:
    """Resolve the verbosity level for a turn: the level mapped to its task_type,
    else its intent, else the default. Always one of LEVELS."""
    levels = levels_map()
    for key in (getattr(classification, "task_type", None), getattr(classification, "intent", None)):
        if key and key in levels:
            return levels[key]
    return default_level()


def normalize(level: str | None) -> str | None:
    """Coerce a one-shot override token to a known level, or None if invalid."""
    if level is None:
        return None
    low = level.lower()
    return low if low in LEVELS else None


def directive_text(level: str | None) -> str | None:
    """The system-prompt line for a level, or None for normal/unknown (no-op).
    The persona appends this under its body, so it shapes length, not voice."""
    if level == "terse":
        return ("## Response length\n\nBe terse. Answer in as few words as the question "
                "honestly allows — no preamble, no recap, no bullet padding. If one sentence "
                "suffices, stop there.")
    if level == "deep":
        return ("## Response length\n\nBe thorough. Give the full reasoning and the tradeoffs, "
                "structure the answer, and name explicitly what is uncertain or undecided.")
    return None  # normal (and any unknown) keeps the persona's natural length
