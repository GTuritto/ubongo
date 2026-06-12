"""The approval gate for authored skills (Phase 3): approve / reject / rollback.

This is the human boundary the whole experiment hinges on. A drafted candidate
lives in quarantine, invisible to the runtime; only `approve` materializes it
into the live `config/skills/` directory and reloads the registry so it becomes a
real, discoverable capability. Mirrors `evolution.promotion` (the GP approval
flow) in shape.

Versioned backups (user requirement): `approve` never destroys a prior version.
If a skill of the same name already exists, it is copied to
`config/skills_backups/<name>/<stamp>/` before being overwritten, and the backup
path is recorded on the `authored_skills` row. `rollback` restores the most
recent backup (or unregisters the skill when there was no prior version), so
authoring an updated skill is fully reversible.

Re-validation happens again at approve time (schema + the command-skill risk
floor), so a tampered or stale row cannot register a skill that would not pass
the draft gate.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from ubongo import context, events, skills
from ubongo.authoring.candidate import SkillCandidate
from ubongo.authoring.quarantine import write_candidate_folder
from ubongo.authoring.validation import CandidateInvalid, validate
from ubongo.memory import authoring_state
from ubongo.memory import store, vault

logger = logging.getLogger("ubongo.authoring.promotion")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_BACKUPS_DIR = _REPO_ROOT / "config" / "skills_backups"

_backups_dir: Path = _DEFAULT_BACKUPS_DIR


class PromotionError(Exception):
    """A user-facing approval-gate failure (unknown id, not a draft, nothing to
    roll back, invalid candidate at the boundary)."""


@dataclass(frozen=True)
class ApproveResult:
    candidate_id: int
    name: str
    backed_up: bool
    backup_path: str | None


@dataclass(frozen=True)
class RejectResult:
    candidate_id: int
    name: str


@dataclass(frozen=True)
class RollbackResult:
    name: str
    restored: bool  # True = restored a prior version; False = unregistered
    backup_path: str | None


def set_backups_dir(path: Path | None) -> None:
    """Override the backups directory (test hook). None resets to default."""
    global _backups_dir
    _backups_dir = Path(path) if path is not None else _DEFAULT_BACKUPS_DIR


def backups_dir() -> Path:
    return _backups_dir


# --- helpers ----------------------------------------------------------------


def _reload_registry() -> None:
    """Make a registration/rollback take effect in the running process."""
    skills.reload()
    context.reload()


def _safe_stamp(iso: str) -> str:
    """Turn an ISO timestamp into a filesystem-safe directory name."""
    return iso.replace(":", "-").replace(".", "-")


def _backup_existing(name: str, live_dir: Path) -> str:
    """Copy the live skill folder to config/skills_backups/<name>/<stamp>/ and
    return the backup path. Caller guarantees the folder exists."""
    base = _backups_dir / name
    base.mkdir(parents=True, exist_ok=True)
    stamp = _safe_stamp(store.now_iso())
    dest = base / stamp
    n = 1
    while dest.exists():  # same-stamp collision within one run
        dest = base / f"{stamp}-{n}"
        n += 1
    shutil.copytree(live_dir / name, dest)
    logger.info("authoring_backup", extra={"skill_name": name, "path": str(dest)})
    return str(dest)


def _list_backups(name: str) -> list[Path]:
    """All backups for a name, oldest first (timestamp dir names sort)."""
    base = _backups_dir / name
    if not base.exists():
        return []
    return sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name)


def _audit(line: str) -> None:
    try:
        vault.append_audit_entry("authoring", line)
    except Exception as exc:  # audit is best-effort, never blocks a decision
        logger.warning("authoring_audit_failed", extra={"cause": str(exc)})


def _load_draft(candidate_id: int) -> dict:
    row = authoring_state.get_authored_skill(candidate_id)
    if row is None:
        raise PromotionError(f"no authored candidate #{candidate_id}")
    if row["status"] != "draft":
        raise PromotionError(
            f"candidate #{candidate_id} is '{row['status']}', not a draft "
            "(only drafts can be approved/rejected)"
        )
    return row


def _latest_approved_row(name: str) -> dict | None:
    rows = [r for r in authoring_state.authored_skills(status="approved", limit=200) if r["name"] == name]
    return rows[0] if rows else None  # authored_skills returns newest first


# --- the gate ---------------------------------------------------------------


def approve(candidate_id: int) -> ApproveResult:
    """Materialize a drafted candidate into the live skills dir, backing up any
    existing version first, then reload so it becomes discoverable. Raises
    PromotionError on a bad id / non-draft / invalid candidate."""
    row = _load_draft(candidate_id)
    candidate = SkillCandidate.from_dict(row["candidate"])
    try:
        candidate = validate(candidate)  # re-enforce schema + command risk floor
    except CandidateInvalid as exc:
        raise PromotionError(f"candidate #{candidate_id} fails validation: {exc}") from None

    live_dir = skills.skills_dir()
    target = live_dir / candidate.name
    backup_path: str | None = None
    if target.exists():
        backup_path = _backup_existing(candidate.name, live_dir)
        shutil.rmtree(target)

    write_candidate_folder(candidate, base=live_dir)
    _reload_registry()

    authoring_state.update_authored_skill(
        candidate_id, status="approved", backup_path=backup_path, decided_at=store.now_iso()
    )
    _audit(
        f"approved #{candidate_id} '{candidate.name}' (risk={candidate.risk}) -> registered"
        + (f"; backed up prior version to {backup_path}" if backup_path else "")
    )
    events.dispatch("authoring_decision", {
        "event": "approved", "id": candidate_id, "name": candidate.name,
        "backed_up": backup_path is not None,
    })
    logger.info("authoring_approved",
                extra={"id": candidate_id, "skill_name": candidate.name,
                       "backed_up": backup_path is not None})
    return ApproveResult(candidate_id, candidate.name, backup_path is not None, backup_path)


def reject(candidate_id: int) -> RejectResult:
    """Reject a drafted candidate: mark it rejected, leave the quarantine folder
    in place. Raises PromotionError on a bad id / non-draft."""
    row = _load_draft(candidate_id)
    authoring_state.update_authored_skill(candidate_id, status="rejected", decided_at=store.now_iso())
    _audit(f"rejected #{candidate_id} '{row['name']}'")
    events.dispatch("authoring_decision",
                    {"event": "rejected", "id": candidate_id, "name": row["name"]})
    logger.info("authoring_rejected", extra={"id": candidate_id, "skill_name": row["name"]})
    return RejectResult(candidate_id, row["name"])


def rollback(name: str) -> RollbackResult:
    """Roll back a live authored skill: restore its most recent backup (the prior
    version) if one exists, else unregister it. Reloads the registry and marks
    the latest approved row rolled_back. Raises PromotionError if there is nothing
    live to roll back."""
    live_dir = skills.skills_dir()
    target = live_dir / name
    backups = _list_backups(name)
    if not target.exists() and not backups:
        raise PromotionError(f"no live skill '{name}' to roll back")

    restored = False
    restored_from: str | None = None
    if backups:
        latest = backups[-1]
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(latest, target)
        restored = True
        restored_from = str(latest)
        # The restored backup is consumed so a second rollback steps back further
        # (or unregisters if it was the last one).
        shutil.rmtree(latest)
    elif target.exists():
        shutil.rmtree(target)

    _reload_registry()

    approved = _latest_approved_row(name)
    if approved is not None:
        authoring_state.update_authored_skill(approved["id"], status="rolled_back",
                                    decided_at=store.now_iso())
    _audit(
        f"rolled back '{name}' -> "
        + (f"restored prior version from {restored_from}" if restored else "unregistered")
    )
    events.dispatch("authoring_decision",
                    {"event": "rolled_back", "name": name, "restored": restored})
    logger.info("authoring_rolledback", extra={"skill_name": name, "restored": restored})
    return RollbackResult(name, restored, restored_from)
