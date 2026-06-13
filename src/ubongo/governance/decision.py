"""Decision matrix (Phase 14e).

`decide()` scores three signals — risk, confidence, reversibility — and
combines them via rules loaded from `config/governance.yaml` into one of four
actions. Rules are evaluated in priority order: safety (require_approval)
before answer-quality (reject) before clarity (ask_clarification).

| # | Condition                                          | Action            |
|---|----------------------------------------------------|-------------------|
| 1 | risk in require_approval.risks (destructive)       | require_approval  |
| 2 | risk == high AND reversibility == irreversible     | require_approval  |
| 3 | evaluator confidence present AND < reject floor    | reject            |
| 4 | command turn AND classifier confidence < floor     | ask_clarification |
| 5 | otherwise                                          | auto              |

Thresholds and rules are data (`governance.yaml`); this module is the only
place that knows how the three scores combine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from ubongo.config import load_governance
from ubongo.governance.confidence import has_evaluator_signal, score_confidence
from ubongo.governance.reversibility import Reversibility, score_reversibility
from ubongo.governance.risk import RiskLevel, score_risk

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
    # The scored signals, so master can persist them without re-scoring.
    risk: str | None = None
    confidence: float | None = None
    reversibility: str | None = None


def decide(
    classification,
    workflow,
    workflow_result,
    *,
    message: str,
    governance: dict | None = None,
) -> Decision:
    """Return the governance decision for this turn.

    `workflow` is the planned Workflow (for reversibility scoring);
    `workflow_result` carries the evaluator confidence; `message` is the raw
    user input (for the destructive-keyword backstop). `governance` overrides
    the loaded `governance.yaml` (tests).
    """
    gov = governance if governance is not None else load_governance()
    thresholds = gov.get("thresholds", {}) or {}
    reject_below = float(thresholds.get("reject_below_confidence", 0.2))
    clarify_below = float(thresholds.get("clarification_below_confidence", 0.5))
    approval = gov.get("require_approval", {}) or {}
    approval_risks = set(approval.get("risks", []) or [])
    gate_irreversible_high = bool(approval.get("irreversible_high_risk", True))
    keywords = gov.get("destructive_keywords", []) or []

    risk = score_risk(classification, message, keywords)
    # Candidate 20 (ADR-0016): a workflow that ran the Connector escalates risk
    # to at least the highest declared risk among the enabled MCP servers (the
    # per-server `risk:` config). Config-read only — no SDK import.
    if "connector" in (getattr(workflow, "agents", ()) or ()):
        try:
            from ubongo.mcp import client as _mcp_client
            server_risk = _mcp_client.max_enabled_risk()
        except Exception:
            server_risk = None
        if server_risk is not None:
            server_level = RiskLevel(server_risk)
            if list(RiskLevel).index(server_level) > list(RiskLevel).index(risk):
                risk = server_level
    confidence = score_confidence(classification, workflow_result)
    reversibility = score_reversibility(workflow)
    scored = {
        "risk": risk.value,
        "confidence": confidence,
        "reversibility": reversibility.value,
    }

    # Rule 1 — a destructive (or otherwise approval-listed) risk always gates.
    if risk.value in approval_risks:
        return Decision(Action.REQUIRE_APPROVAL.value, f"risk_{risk.value}", **scored)

    # Rule 2 — high risk that cannot be undone gates.
    if gate_irreversible_high and risk is RiskLevel.HIGH and reversibility is Reversibility.IRREVERSIBLE:
        return Decision(Action.REQUIRE_APPROVAL.value, "irreversible_high_risk", **scored)

    # Rule 2.5 (v0.5 phase 05) — the grant registry. A connector turn touching a
    # capability class with no active grant asks once (first encounter); once
    # granted, later turns in that class fall through to auto. Placed AFTER the
    # safety rules so a destructive connector turn still gates on rule 1 even
    # with a grant. The require_approval verdict here is converted to auto by
    # master's existing `approved=True` bypass on the re-issue, which is also
    # where the grant is written (so the *next* turn auto-proceeds).
    from ubongo.governance import grants as _grants
    ungranted = _grants.ungranted_classes(workflow)
    if ungranted:
        return Decision(
            Action.REQUIRE_APPROVAL.value,
            f"grant_first_encounter:{ungranted[0]}",
            **scored,
        )

    # Rule 3 — the Evaluator judged the answer too weak to stand.
    if has_evaluator_signal(workflow_result) and confidence < reject_below:
        return Decision(
            Action.REJECT.value,
            f"evaluator_confidence_below_floor:{confidence:.2f}",
            **scored,
        )

    # Rule 4 — an under-specified command: the classifier itself was unsure.
    task_type = getattr(classification, "task_type", None)
    classifier_conf = getattr(classification, "confidence", 0.0) or 0.0
    if task_type == "command" and float(classifier_conf) < clarify_below:
        return Decision(
            Action.ASK_CLARIFICATION.value,
            f"command_low_classifier_confidence:{float(classifier_conf):.2f}",
            **scored,
        )

    # Rule 5 — nothing tripped: proceed.
    return Decision(Action.AUTO.value, None, **scored)
