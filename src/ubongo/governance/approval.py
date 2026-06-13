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


@dataclass(frozen=True)
class PendingApproval:
    """A require_approval turn held for a decision the user has not yet made —
    the persisted, resumable record (v0.5 phase 03). It carries everything
    `master.resume_approval` needs to re-issue the turn, so resume no longer
    depends on the requesting channel still holding it in memory.
    """

    decision_id: int
    message: str
    persona: str
    auto_mode: bool
    summary: str
    why: str
    status: str            # pending | approved | declined
    created_at: str
    resolved_at: str | None = None

    @classmethod
    def from_row(cls, row: dict) -> "PendingApproval":
        return cls(
            decision_id=row["decision_id"],
            message=row["message"],
            persona=row["persona"],
            auto_mode=bool(row["auto_mode"]),
            summary=row["summary"],
            why=row["why"],
            status=row["status"],
            created_at=row["created_at"],
            resolved_at=row.get("resolved_at"),
        )

    @property
    def request(self) -> "ApprovalRequest":
        """The y/n/why surface for this pending turn."""
        return ApprovalRequest(self.decision_id, self.summary, self.why)


def list_pending() -> list["PendingApproval"]:
    """Every still-open approval, oldest first — the `/pending` surface."""
    from ubongo.memory import trace
    return [PendingApproval.from_row(r) for r in trace.open_pending_approvals()]


def get_pending(decision_id: int) -> "PendingApproval | None":
    from ubongo.memory import trace
    row = trace.get_pending_approval(decision_id)
    return PendingApproval.from_row(row) if row is not None else None


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
