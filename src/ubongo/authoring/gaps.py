"""Capability-gap inference for the autonomous authoring daemon (Phase 4a).

`next_gap()` is a deterministic, bounded read over recent turns: it looks for an
intent that keeps showing up but matched NO skill (the classifier returned
`suggested_skill = null`), which is the signal that the system lacks a capability
the user repeatedly reaches for. It excludes gaps the daemon has already worked
(so it does not re-draft the same skill every cycle) and returns the most
frequent remaining one, with a representative user message to ground the draft.

Pure and side-effect-free: the daemon decides what to do with the returned gap.
"""

from __future__ import annotations

from dataclasses import dataclass

from ubongo.memory import authoring_state
from ubongo.memory import store
from ubongo.memory import trace

_DEFAULT_LIMIT = 200
_DEFAULT_MIN_OCCURRENCES = 2


@dataclass(frozen=True)
class Gap:
    """A recurring unmet capability. `intent` is the gap's identity (what the
    daemon records so it is not re-worked); `description` is what gets handed to
    the skill drafter; `occurrences` is how many recent turns hit it."""

    intent: str
    description: str
    occurrences: int


def next_gap(*, limit: int = _DEFAULT_LIMIT, min_occurrences: int = _DEFAULT_MIN_OCCURRENCES) -> Gap | None:
    """The most frequent recurring intent that matched no skill and has not been
    worked yet, or None. Deterministic (frequency desc, then intent asc)."""
    rows = trace.recent_workflow_classifications(limit)
    tally: dict[str, int] = {}
    sample: dict[str, str] = {}
    for r in rows:
        cls = r.get("classification") or {}
        intent = str(cls.get("intent") or "").strip()
        if not intent:
            continue
        if cls.get("suggested_skill"):  # a skill already covers this intent
            continue
        tally[intent] = tally.get(intent, 0) + 1
        sample.setdefault(intent, r.get("message") or "")

    if not tally:
        return None
    worked = authoring_state.worked_authoring_gaps()
    candidates = [
        (intent, n) for intent, n in tally.items()
        if n >= min_occurrences and intent not in worked
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda t: (-t[1], t[0]))
    intent, n = candidates[0]

    snippet = " ".join((sample.get(intent) or "").split())[:200]
    description = (
        f'A reusable skill for recurring "{intent}" requests that currently match '
        "no existing skill."
        + (f' Example user message: "{snippet}"' if snippet else "")
    )
    return Gap(intent=intent, description=description, occurrences=n)
