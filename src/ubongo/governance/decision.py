"""Decision matrix scaffold. Phase 8 ships an always-auto stub.

Phase 14 will replace `decide()` with the real risk/confidence/reversibility
matrix that reads from `governance.yaml`. The function signature is the one
Phase 14 will use, so call sites don't churn when rules land.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("ubongo.governance.decision")


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
    """v0.1 Phase 8 stub: every turn is auto.

    Real matrix ships Phase 14. `classification` and `workflow` are accepted
    positionally; `evaluator_confidence` is keyword-only so Phase 10 can wire
    the Evaluator without changing earlier call sites.
    """
    return Decision(action=Action.AUTO.value, reason=None)
