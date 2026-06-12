"""Authoring state — the self-extension tables (moved from store.py, v0.5 phase 02).

Owns authored_skills (the quarantine ledger) and authoring_runs / authoring_state
(the daemon's budget and control rows). Pure CRUD over store.connection(); the
drafting/validation/promotion logic lives in ubongo.authoring.* (ADR-0013).
"""

from __future__ import annotations

from datetime import timedelta

from ubongo.memory.store import _now, _parse_iso, connection, now_iso

# One row per drafted skill candidate. Phase 1 writes 'draft' rows; the approval
# gate (Phase 3) updates status / backup_path / decided_at, and the evaluation
# (Phase 2) sets quality.

_AUTHORED_COLUMNS = (
    "id, name, description, status, generation, source, candidate, "
    "quarantine_path, backup_path, quality, created_at, decided_at"
)


def _authored_row(r: sqlite3.Row) -> dict:
    import json as _json

    d = dict(r)
    raw = d.get("candidate")
    d["candidate"] = _json.loads(raw) if raw else {}
    return d


def append_authored_skill(
    *,
    name: str,
    description: str,
    status: str = "draft",
    generation: int = 1,
    source: str = "manual",
    candidate: dict,
    quarantine_path: str | None = None,
    created_at: str | None = None,
) -> int:
    """Persist one authored_skills row (the candidate dict is stored as JSON).
    Returns the new id."""
    import json as _json

    conn = connection()
    cursor = conn.execute(
        """
        INSERT INTO authored_skills
            (name, description, status, generation, source, candidate,
             quarantine_path, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            description,
            status,
            generation,
            source,
            _json.dumps(candidate),
            quarantine_path,
            created_at or now_iso(),
        ),
    )
    return int(cursor.lastrowid)


def get_authored_skill(skill_id: int) -> dict | None:
    conn = connection()
    r = conn.execute(
        f"SELECT {_AUTHORED_COLUMNS} FROM authored_skills WHERE id = ?", (skill_id,)
    ).fetchone()
    return _authored_row(r) if r is not None else None


def authored_skills(status: str | None = None, limit: int = 50) -> list[dict]:
    """List authored-skill rows, newest first, optionally filtered by status."""
    conn = connection()
    if status is not None:
        rows = conn.execute(
            f"SELECT {_AUTHORED_COLUMNS} FROM authored_skills WHERE status = ? "
            "ORDER BY id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {_AUTHORED_COLUMNS} FROM authored_skills ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_authored_row(r) for r in rows]


def max_authored_generation(name: str) -> int:
    """Highest generation recorded for an authored-skill name, or 0 if none."""
    conn = connection()
    r = conn.execute(
        "SELECT COALESCE(MAX(generation), 0) AS g FROM authored_skills WHERE name = ?",
        (name,),
    ).fetchone()
    return int(r["g"]) if r is not None else 0


def update_authored_skill(
    skill_id: int,
    *,
    status: str | None = None,
    backup_path: str | None = None,
    quality: float | None = None,
    decided_at: str | None = None,
) -> bool:
    """Patch the mutable fields of an authored-skill row (Phase 2/3). Only the
    provided fields change. Returns True if a row was updated."""
    sets: list[str] = []
    params: list = []
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if backup_path is not None:
        sets.append("backup_path = ?")
        params.append(backup_path)
    if quality is not None:
        sets.append("quality = ?")
        params.append(quality)
    if decided_at is not None:
        sets.append("decided_at = ?")
        params.append(decided_at)
    if not sets:
        return False
    params.append(skill_id)
    conn = connection()
    cur = conn.execute(
        f"UPDATE authored_skills SET {', '.join(sets)} WHERE id = ?", params
    )
    return cur.rowcount > 0


def auto_drafts_unevaluated(limit: int = 5) -> list[dict]:
    """Auto-authored drafts that have no quality score yet (a crash between
    persist and evaluate leaves these). The daemon re-evaluates them on its next
    cycle rather than drafting anew."""
    conn = connection()
    rows = conn.execute(
        f"SELECT {_AUTHORED_COLUMNS} FROM authored_skills "
        "WHERE source = 'auto' AND status = 'draft' AND quality IS NULL "
        "ORDER BY id ASC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_authored_row(r) for r in rows]


# --- Authoring daemon runs + control state (Phase 4) ------------------------
# Mirrors the evolution_runs / evolution_state accessors: per-cycle rows double
# as the rolling-hour throttle window, and a single control row persists the
# daemon's paused/running/off state across restarts.


def start_authoring_run(*, gap: str | None, started_at: str | None = None) -> int:
    conn = connection()
    cursor = conn.execute(
        "INSERT INTO authoring_runs (gap, calls_spent, outcome, started_at) "
        "VALUES (?, 0, 'started', ?)",
        (gap, started_at or now_iso()),
    )
    return int(cursor.lastrowid)


def finish_authoring_run(
    run_id: int,
    *,
    calls_spent: int,
    outcome: str,
    candidate_id: int | None = None,
    ended_at: str | None = None,
) -> None:
    conn = connection()
    conn.execute(
        "UPDATE authoring_runs SET calls_spent = ?, outcome = ?, candidate_id = ?, "
        "ended_at = ? WHERE id = ?",
        (calls_spent, outcome, candidate_id, ended_at or now_iso(), run_id),
    )


def authoring_calls_in_last_hour() -> int:
    cutoff = (_now() - timedelta(hours=1)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    conn = connection()
    row = conn.execute(
        "SELECT COALESCE(SUM(calls_spent), 0) AS n FROM authoring_runs "
        "WHERE ended_at IS NOT NULL AND ended_at >= ?",
        (cutoff,),
    ).fetchone()
    return int(row["n"]) if row and row["n"] is not None else 0


def authoring_seconds_since_last_cycle() -> float | None:
    conn = connection()
    row = conn.execute(
        "SELECT MAX(ended_at) AS t FROM authoring_runs WHERE ended_at IS NOT NULL"
    ).fetchone()
    if not row or row["t"] is None:
        return None
    return (_now() - _parse_iso(row["t"])).total_seconds()


def authoring_runs_recent(n: int = 10) -> list[dict]:
    if n <= 0:
        return []
    conn = connection()
    rows = conn.execute(
        "SELECT id, gap, candidate_id, calls_spent, outcome, started_at, ended_at "
        "FROM authoring_runs ORDER BY id DESC LIMIT ?",
        (n,),
    ).fetchall()
    return [dict(r) for r in rows]


def worked_authoring_gaps() -> set[str]:
    """The gaps the daemon has already worked (drafted, not aborted), so it does
    not re-draft the same capability every cycle."""
    conn = connection()
    rows = conn.execute(
        "SELECT DISTINCT gap FROM authoring_runs "
        "WHERE gap IS NOT NULL AND outcome != 'aborted'"
    ).fetchall()
    return {r["gap"] for r in rows}


def get_authoring_status() -> str:
    """The daemon control status; defaults to 'paused' when unset so it never
    auto-drafts on first launch."""
    conn = connection()
    row = conn.execute("SELECT status FROM authoring_state WHERE id = 1").fetchone()
    return row["status"] if row else "paused"


def set_authoring_status(status: str) -> None:
    if status not in ("running", "paused", "off"):
        raise ValueError(f"invalid authoring status: {status}")
    conn = connection()
    conn.execute(
        "INSERT INTO authoring_state (id, status, updated_at) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at",
        (status, now_iso()),
    )


