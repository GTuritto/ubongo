"""Risk scoring (Phase 14b).

The classifier already emits a `risk` field, but it is one small model's call
and it flakes. `score_risk` takes the higher of the classifier's rating and a
keyword backstop: any message containing a configured destructive substring is
escalated to `destructive` regardless of what the classifier said. This keeps
obviously-dangerous requests deterministically gated.
"""

from __future__ import annotations

from enum import Enum


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    DESTRUCTIVE = "destructive"


def from_classifier(risk: str | None) -> RiskLevel:
    """Map the classifier's `risk` string to a RiskLevel.

    An unknown / missing value is treated as `low` — the classifier failed to
    rate it, and the keyword backstop still catches the dangerous cases.
    """
    try:
        return RiskLevel(risk or "low")
    except ValueError:
        return RiskLevel.LOW


def score_risk(classification, message: str, destructive_keywords: list[str]) -> RiskLevel:
    """Return the governing risk level for this turn.

    The higher of (a) the classifier's `risk` and (b) `destructive` when the
    message matches any configured destructive keyword (case-insensitive
    substring).
    """
    classifier_level = from_classifier(getattr(classification, "risk", None))
    lowered = (message or "").lower()
    for keyword in destructive_keywords or []:
        if keyword and keyword.lower() in lowered:
            return RiskLevel.DESTRUCTIVE
    return classifier_level
