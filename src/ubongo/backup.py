"""Backup + portability (v0.5 phase 07): an instance is its data + config.

Identity rides on the Phase-02 layering — Ubongo *is* its SQLite memory, its
vault, and its config (settings/governance/jobs/personas/skills). `create_backup`
writes a portable `tar.gz` of exactly those; **never `.env`** (secrets stay out)
and never the throwaway `data/profiles/`. There is no install log to replay —
capabilities are the human-approved config allowlist.

`restore_backup` unpacks an archive into a target checkout. **Grants do not
migrate**: by default the restore re-arms them (revokes active grants in the
restored DB), so the first connector turn on a new envelope asks again — a moved
instance crosses a new trust boundary. A same-machine disaster-recovery restore
can keep them with `fresh_grants=False`.
"""

from __future__ import annotations

import logging
import sqlite3
import tarfile
from pathlib import Path

logger = logging.getLogger("ubongo.backup")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# What an instance is: the DB files + the vault + the config tree. Listed
# explicitly so the archive never sweeps in secrets (.env, root-level) or the
# disposable profiler dumps (data/profiles/).
_DB_FILES = ("data/ubongo.db", "data/ubongo.db-wal", "data/ubongo.db-shm")
_DIRS = ("vault", "config")
_EXCLUDE_NAMES = {".env"}


def _stamp(now_iso_fn) -> str:
    return now_iso_fn().replace(":", "").replace(".", "-")


def create_backup(dest: Path, *, root: Path | None = None, now_iso_fn=None) -> Path:
    """Write a portable archive of the instance under `dest`. If `dest` is a
    directory the archive is named `ubongo-backup-<stamp>.tar.gz` inside it;
    otherwise `dest` is the archive path. Returns the archive path."""
    root = root or _REPO_ROOT
    if now_iso_fn is None:
        from ubongo.memory.store import now_iso as now_iso_fn

    if dest.is_dir() or dest.suffix == "":
        dest.mkdir(parents=True, exist_ok=True)
        archive = dest / f"ubongo-backup-{_stamp(now_iso_fn)}.tar.gz"
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        archive = dest

    def _filter(info: tarfile.TarInfo):
        # Defence in depth: never let an excluded basename ride along.
        if Path(info.name).name in _EXCLUDE_NAMES:
            return None
        return info

    with tarfile.open(archive, "w:gz") as tar:
        for rel in _DB_FILES:
            p = root / rel
            if p.exists():
                tar.add(p, arcname=rel, filter=_filter)
        for rel in _DIRS:
            p = root / rel
            if p.exists():
                tar.add(p, arcname=rel, filter=_filter)
    logger.info("backup_written", extra={"archive": str(archive)})
    return archive


def _safe_members(tar: tarfile.TarFile, target: Path) -> list[tarfile.TarInfo]:
    """Reject path-traversal / absolute members (no `..`, no leading `/`)."""
    safe = []
    target = target.resolve()
    for m in tar.getmembers():
        dest = (target / m.name).resolve()
        if not str(dest).startswith(str(target)):
            raise ValueError(f"unsafe archive member: {m.name}")
        safe.append(m)
    return safe


def _rearm_grants(db_path: Path) -> int:
    """Revoke active grants in a restored DB so a new envelope re-asks. Returns
    the count revoked; best-effort (no DB / no table -> 0)."""
    if not db_path.exists():
        return 0
    try:
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.execute(
                "UPDATE grants SET status = 'revoked', "
                "revoked_at = COALESCE(revoked_at, datetime('now')) WHERE status = 'active'"
            )
            conn.commit()
            return cur.rowcount or 0
        finally:
            conn.close()
    except sqlite3.Error:
        return 0  # no grants table yet (fresh DB) — nothing to re-arm


def restore_backup(archive: Path, target: Path, *, fresh_grants: bool = True) -> int:
    """Unpack `archive` into `target`. By default re-arm grants (revoke active
    rows in the restored DB). Returns the number of grants revoked."""
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        members = _safe_members(tar, target)
        try:
            tar.extractall(target, members=members, filter="data")  # 3.12+/3.11.4+
        except TypeError:
            tar.extractall(target, members=members)  # older 3.11: _safe_members guards
    revoked = _rearm_grants(target / "data" / "ubongo.db") if fresh_grants else 0
    logger.info("backup_restored",
                extra={"target": str(target), "grants_revoked": revoked, "fresh_grants": fresh_grants})
    return revoked
