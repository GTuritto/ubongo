"""Decision matrix.

Phase 10 stub: rejects when evaluator confidence is below the floor; every
other turn still returns `auto`. Phase 14 will replace `decide()` with the
full risk/confidence/reversibility matrix that reads from `governance.yaml`,
but the function signature is the one Phase 14 will use — call sites do
not churn when rules land.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("ubongo.governance.decision")

# Phase 10: hardcoded thresholds. Phase 14 moves these to governance.yaml.
REJECT_BELOW: float = 0.2


class Action(str, Enum):
    AUTO = "auto"
    ASK_CLARIFICATION = "ask_clarification"
    REQUIRE_APPROVAL = "require_approval"
    REJECT = "reject"


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str | None = None


def decide(classification, workflow, *, evaluator_confidence: float | None = None) -> Decision:
    """Return the decision for this turn.

    Phase 10 rules:
    - evaluator_confidence < 0.2 -> reject
    - everything else            -> auto
    """
    if evaluator_confidence is not None and evaluator_confidence < REJECT_BELOW:
        return Decision(
            action=Action.REJECT.value,
            reason=f"evaluator_confidence_below_floor:{evaluator_confidence:.2f}",
        )
    return Decision(action=Action.AUTO.value, reason=None)
