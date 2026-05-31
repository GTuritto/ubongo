"""Approval requests (Phase 15a).

When the decision matrix returns `require_approval`, the turn cannot just be
delivered — the user has to say yes. This module turns a `Decision` into an
`ApprovalRequest`: a one-line summary for the prompt and a paragraph the user
can read by typing `why`. It is pure logic — the REPL does the actual `input()`
and persistence.
"""

from __future__ import annotations

from dataclasses import dataclass

# Maps a require_approval Decision.reason to a human phrase.
_REASON_PHRASES: dict[str, str] = {
    "risk_destructive": "the request looks destructive",
    "irreversible_high_risk": "the request is high-risk and cannot be undone",
}


@dataclass(frozen=True)
class ApprovalRequest:
    decision_id: int
    summary: str   # one line, shown with the y/n/why prompt
    why: str       # one paragraph, shown when the user types `why`


def _reason_phrase(reason: str | None) -> str:
    if reason and reason in _REASON_PHRASES:
        return _REASON_PHRASES[reason]
    return "the request was flagged by the governance matrix"


def explain(decision, message: str) -> str:
    """The one-paragraph risk explanation shown when the user types `why`."""
    phrase = _reason_phrase(getattr(decision, "reason", None))
    risk = getattr(decision, "risk", None) or "unknown"
    reversibility = getattr(decision, "reversibility", None) or "unknown"
    snippet = (message or "").strip().replace("\n", " ")
    if len(snippet) > 120:
        snippet = snippet[:117] + "..."
    return (
        f'Governance held this turn because {phrase}: it scored risk={risk}, '
        f'reversibility={reversibility}. Request: "{snippet}". '
        f"Approving delivers the answer and records your approval; declining "
        f"discards it. Nothing has been executed — this gate runs before any "
        f"action would be taken."
    )


def build_request(decision_id: int, decision, message: str) -> ApprovalRequest:
    """Build the ApprovalRequest for a require_approval turn."""
    risk = getattr(decision, "risk", None) or "unknown"
    summary = (
        f"Governance flagged this turn for approval "
        f"(risk={risk}, reason={getattr(decision, 'reason', None) or '—'})."
    )
    return ApprovalRequest(
        decision_id=decision_id,
        summary=summary,
        why=explain(decision, message),
    )
