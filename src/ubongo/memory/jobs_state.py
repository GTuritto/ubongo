"""Standing-jobs state (v0.5 phase 06): runtime rows for proactive jobs.

Three concerns, all pure CRUD over ``store.connection()`` (the scheduling,
delivery, and park-and-raise logic lives in ``ubongo.jobs.*``):

- ``standing_jobs`` — per-job runtime state (last_run / next_run / last_outcome),
  keyed by name. ``config/jobs.yaml`` is the source of truth for *what* a job is;
  this row is *when* it last ran, so the schedule survives a restart.
- ``job_runs`` — one row per cycle (the proactive-policy verdict + the linked
  ``pending_approvals`` decision when parked); doubles as the rolling-hour
  throttle window.
- ``jobs_state`` — the single control row (running / paused / off), mirroring
  ``evolution_state`` / ``authoring_state`` so the daemon comes back paused.
"""

from __future__ import annotations

from datetime import timedelta

from ubongo.memory.store import _now, _parse_iso, connection, now_iso


# --- per-job runtime rows ---------------------------------------------------


def ensure_job(name: str) -> None:
    """Create the runtime row for a config-defined job if absent (no clobber of
    an existing row's last_run / next_run). Called when jobs.yaml is loaded."""
    connection().execute(
        "INSERT OR IGNORE INTO standing_jobs (name, created_at) VALUES (?, ?)",
        (name, now_iso()),
    )


def get_job(name: str) -> dict | None:
    row = connection().execute(
        "SELECT name, last_run, next_run, last_outcome, created_at "
        "FROM standing_jobs WHERE name = ?",
        (name,),
    ).fetchone()
    return dict(row) if row is not None else None


def all_jobs() -> list[dict]:
    rows = connection().execute(
        "SELECT name, last_run, next_run, last_outcome, created_at "
        "FROM standing_jobs ORDER BY name ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def mark_run(name: str, *, last_run: str, next_run: str | None, last_outcome: str) -> None:
    """Record that a job ran: update last_run / next_run / last_outcome."""
    connection().execute(
        "UPDATE standing_jobs SET last_run = ?, next_run = ?, last_outcome = ? "
        "WHERE name = ?",
        (last_run, next_run, last_outcome, name),
    )


# --- job_runs audit + throttle ---------------------------------------------


def start_job_run(job_name: str, *, started_at: str | None = None) -> int:
    cur = connection().execute(
        "INSERT INTO job_runs (job_name, outcome, started_at) VALUES (?, 'error', ?)",
        (job_name, started_at or now_iso()),
    )
    return int(cur.lastrowid)


def finish_job_run(
    run_id: int,
    *,
    outcome: str,
    decision_id: int | None = None,
    detail: str | None = None,
    ended_at: str | None = None,
) -> None:
    connection().execute(
        "UPDATE job_runs SET outcome = ?, decision_id = ?, detail = ?, ended_at = ? "
        "WHERE id = ?",
        (outcome, decision_id, detail, ended_at or now_iso(), run_id),
    )


def record_job_run(
    job_name: str, *, outcome: str, decision_id: int | None = None, detail: str | None = None
) -> int:
    """One-shot insert of a finished job_run (e.g. a TTL-expiry 'skipped' row)."""
    ts = now_iso()
    cur = connection().execute(
        "INSERT INTO job_runs (job_name, outcome, decision_id, detail, started_at, ended_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (job_name, outcome, decision_id, detail, ts, ts),
    )
    return int(cur.lastrowid)


def job_runs_recent(n: int = 10) -> list[dict]:
    if n <= 0:
        return []
    rows = connection().execute(
        "SELECT id, job_name, outcome, decision_id, detail, started_at, ended_at "
        "FROM job_runs ORDER BY id DESC LIMIT ?",
        (n,),
    ).fetchall()
    return [dict(r) for r in rows]


def runs_in_last_hour() -> int:
    """Count of finished job cycles in the trailing hour — the throttle window."""
    cutoff = (_now() - timedelta(hours=1)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    row = connection().execute(
        "SELECT COUNT(*) AS n FROM job_runs WHERE ended_at IS NOT NULL AND ended_at >= ?",
        (cutoff,),
    ).fetchone()
    return int(row["n"]) if row and row["n"] is not None else 0


def seconds_since_last_cycle() -> float | None:
    row = connection().execute(
        "SELECT MAX(ended_at) AS t FROM job_runs WHERE ended_at IS NOT NULL"
    ).fetchone()
    if not row or row["t"] is None:
        return None
    return (_now() - _parse_iso(row["t"])).total_seconds()


def expired_parked_decisions(ttl_seconds: float) -> list[dict]:
    """Parked raises older than the TTL whose pending_approval is still pending —
    the default-deny set (AC-7). Returns [{job_name, decision_id}], so the loop
    auto-declines each and logs a 'skipped' cycle. Joins job_runs (parked) to
    pending_approvals (still pending)."""
    cutoff = (_now() - timedelta(seconds=ttl_seconds)).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    rows = connection().execute(
        "SELECT jr.job_name AS job_name, jr.decision_id AS decision_id "
        "FROM job_runs jr JOIN pending_approvals pa ON pa.decision_id = jr.decision_id "
        "WHERE jr.outcome = 'parked' AND jr.decision_id IS NOT NULL "
        "AND pa.status = 'pending' AND jr.started_at <= ?",
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


# --- daemon control state (mirrors evolution_state / authoring_state) -------


def get_jobs_status() -> str:
    """Control status; defaults to 'paused' when unset so the daemon never speaks
    unprompted on first launch."""
    row = connection().execute("SELECT status FROM jobs_state WHERE id = 1").fetchone()
    return row["status"] if row else "paused"


def set_jobs_status(status: str) -> None:
    if status not in ("running", "paused", "off"):
        raise ValueError(f"invalid jobs status: {status}")
    connection().execute(
        "INSERT INTO jobs_state (id, status, updated_at) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at",
        (status, now_iso()),
    )
