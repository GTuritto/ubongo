"""User-driven authoring entry point behind `/author` (Phase 1f).

`author_skill(description)` runs the full manual path: draft -> validate (with the
command-skill risk floor) -> quarantine, returning an `AuthorOutcome` the REPL
renders. It owns the orchestration the REPL used to lack; the command handler in
`repl.py` only formats the result. The autonomous counterpart is
`authoring/loop.py` (Phase 4), which reuses draft + validate + quarantine the
same way.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ubongo.authoring import quarantine
from ubongo.authoring.candidate import DraftError, SkillCandidate, draft_candidate
from ubongo.authoring.validation import CandidateInvalid, validate
from ubongo.memory import store, vault

logger = logging.getLogger("ubongo.authoring.manual")


class AuthoringError(Exception):
    """A manual authoring run failed (drafting or validation)."""


@dataclass(frozen=True)
class AuthorOutcome:
    candidate_id: int
    candidate: SkillCandidate
    generation: int


def author_skill(description: str, *, source: str = "manual") -> AuthorOutcome:
    """Draft, validate, and quarantine one skill. Raises AuthoringError on failure."""
    try:
        drafted = draft_candidate(description, source=source)
    except DraftError as exc:
        raise AuthoringError(str(exc)) from None
    try:
        candidate = validate(drafted)
    except CandidateInvalid as exc:
        raise AuthoringError(f"drafted skill {drafted.name!r} is invalid: {exc}") from None

    candidate_id = quarantine.persist(candidate, source=source)
    row = store.get_authored_skill(candidate_id)
    generation = int(row["generation"]) if row else 0
    # Audit the draft so /audit authoring shows a trail even before approval.
    try:
        vault.append_audit_entry(
            "authoring",
            f"drafted candidate #{candidate_id} '{candidate.name}' "
            f"(risk={candidate.risk}, command={'yes' if candidate.is_command_skill else 'no'}, "
            f"source={source}) -> quarantined",
        )
    except Exception as exc:  # audit is best-effort, never blocks a draft
        logger.warning("authoring_audit_failed", extra={"cause": str(exc)})

    from ubongo import events

    events.dispatch(
        "authoring_candidate",
        {"id": candidate_id, "name": candidate.name, "source": source},
    )
    return AuthorOutcome(candidate_id=candidate_id, candidate=candidate, generation=generation)
