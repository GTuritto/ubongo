"""Evolution state: evolution_lineage / _evaluations / _runs / _state,
pending_promotions, active_evolutions. Pure CRUD over store.connection();
loop/selection/promotion logic lives in ubongo.evolution.*; the live-swap read
paths (context.build_system_prompt, router.route_workflow) consult
active_evolution() here."""

from __future__ import annotations

from datetime import timedelta

from ubongo.memory.store import _now, _parse_iso, connection, now_iso

# The `evolution_lineage` table (and the downstream evaluation / promotion
# tables) already ships in schema.sql. Phase 16 only writes lineage rows; the
# rest stay empty until Phases 17 / 19.


def append_lineage_variant(
    *,
    target: str,
    parent_id: int | None,
    generation: int,
    variant_text: str,
    variant_metadata: dict | None,
    created_at: str | None = None,
) -> int:
    """Persist one evolution_lineage row, returns the new id.

    `parent_id` points to the lineage row the variant descends from — the
    currently-promoted active variant when one exists, else NULL (Phase 16 has
    no promotions yet, so it is always NULL). `variant_metadata` records
    provenance (strategy, base source, perturbation deltas) as JSON.
    """
    import json as _json

    conn = connection()
    cursor = conn.execute(
        """
        INSERT INTO evolution_lineage
            (target, parent_id, generation, variant_text, variant_metadata,
             created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            target,
            parent_id,
            generation,
            variant_text,
            _json.dumps(variant_metadata) if variant_metadata is not None else None,
            created_at or now_iso(),
        ),
    )
    return int(cursor.lastrowid)


def lineage_for_target(target: str, generation: int | None = None) -> list[dict]:
    """Return evolution_lineage rows for a target, oldest first.

    Optionally filtered to a single generation. `variant_metadata` is parsed
    back to a dict (or {} when null / unparseable). Used by the REPL, tests,
    and Phase 17's evaluation step.
    """
    import json as _json

    conn = connection()
    if generation is None:
        rows = conn.execute(
            """
            SELECT id, target, parent_id, generation, variant_text,
                   variant_metadata, created_at
            FROM evolution_lineage
            WHERE target = ?
            ORDER BY id
            """,
            (target,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, target, parent_id, generation, variant_text,
                   variant_metadata, created_at
            FROM evolution_lineage
            WHERE target = ? AND generation = ?
            ORDER BY id
            """,
            (target, generation),
        ).fetchall()

    out: list[dict] = []
    for row in rows:
        try:
            meta = _json.loads(row["variant_metadata"]) if row["variant_metadata"] else {}
        except Exception:
            meta = {}
        out.append({
            "id": row["id"],
            "target": row["target"],
            "parent_id": row["parent_id"],
            "generation": row["generation"],
            "variant_text": row["variant_text"],
            "variant_metadata": meta,
            "created_at": row["created_at"],
        })
    return out


def lineage_row(lineage_id: int) -> dict | None:
    """Return a single evolution_lineage row by id (target, generation,
    variant_text, parent_id), or None."""
    conn = connection()
    r = conn.execute(
        "SELECT id, target, parent_id, generation, variant_text FROM evolution_lineage WHERE id = ?",
        (lineage_id,),
    ).fetchone()
    if r is None:
        return None
    return {"id": r["id"], "target": r["target"], "parent_id": r["parent_id"],
            "generation": r["generation"], "variant_text": r["variant_text"]}


def max_lineage_generation(target: str) -> int:
    """Return the highest generation recorded for a target, or 0 if none.

    `record_variants` uses this to compute the next generation (0 → first run
    writes generation 1).
    """
    conn = connection()
    row = conn.execute(
        "SELECT MAX(generation) AS g FROM evolution_lineage WHERE target = ?",
        (target,),
    ).fetchone()
    return int(row["g"]) if row and row["g"] is not None else 0


def active_lineage_id(target: str) -> int | None:
    """Return the promoted lineage id for a target, or None when unpromoted.

    Reads `active_evolutions` — empty in Phase 16, so this is always None. It
    is the parent pointer source for new variants and the seam Phase 19 fills
    when it writes promotions.
    """
    conn = connection()
    row = conn.execute(
        "SELECT lineage_id FROM active_evolutions WHERE target = ?",
        (target,),
    ).fetchone()
    return int(row["lineage_id"]) if row else None


# --- Evolution evaluations (Phase 17) ---------------------------------------
# One row per (variant, sample_set): the aggregate metrics + fitness from
# running a lineage variant against the held-out conversation set.


def append_evaluation(
    *,
    lineage_id: int,
    sample_set: str,
    success_rate: float | None,
    cost: float | None,
    latency_ms: float | None,
    hallucination_rate: float | None,
    user_correction_rate: float | None,
    fitness: float,
    evaluated_at: str | None = None,
) -> int:
    """Persist one evolution_evaluations row, returns the new id."""
    conn = connection()
    cursor = conn.execute(
        """
        INSERT INTO evolution_evaluations
            (lineage_id, sample_set, success_rate, cost, latency_ms,
             hallucination_rate, user_correction_rate, fitness, evaluated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            lineage_id,
            sample_set,
            success_rate,
            cost,
            latency_ms,
            hallucination_rate,
            user_correction_rate,
            fitness,
            evaluated_at or now_iso(),
        ),
    )
    return int(cursor.lastrowid)


def evaluations_for_target(target: str, generation: int | None = None) -> list[dict]:
    """Return evaluation rows for a target, joined to their lineage variant,
    ranked best-first (fitness desc, then lineage_id asc — the deterministic
    tiebreak). Powers the `/evaluate` leaderboard and Phase 19 promotion.

    Each dict carries the evaluation metrics plus the variant's `lineage_id`,
    `generation`, `strategy` (from `variant_metadata`), and `variant_text`.
    """
    import json as _json

    conn = connection()
    if generation is None:
        rows = conn.execute(
            """
            SELECT e.id, e.lineage_id, e.sample_set, e.success_rate, e.cost,
                   e.latency_ms, e.hallucination_rate, e.user_correction_rate,
                   e.fitness, e.evaluated_at,
                   l.generation, l.variant_text, l.variant_metadata
            FROM evolution_evaluations e
            JOIN evolution_lineage l ON l.id = e.lineage_id
            WHERE l.target = ?
            ORDER BY e.fitness DESC, e.lineage_id ASC
            """,
            (target,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT e.id, e.lineage_id, e.sample_set, e.success_rate, e.cost,
                   e.latency_ms, e.hallucination_rate, e.user_correction_rate,
                   e.fitness, e.evaluated_at,
                   l.generation, l.variant_text, l.variant_metadata
            FROM evolution_evaluations e
            JOIN evolution_lineage l ON l.id = e.lineage_id
            WHERE l.target = ? AND l.generation = ?
            ORDER BY e.fitness DESC, e.lineage_id ASC
            """,
            (target, generation),
        ).fetchall()

    out: list[dict] = []
    for row in rows:
        try:
            meta = _json.loads(row["variant_metadata"]) if row["variant_metadata"] else {}
        except Exception:
            meta = {}
        out.append({
            "id": row["id"],
            "lineage_id": row["lineage_id"],
            "sample_set": row["sample_set"],
            "success_rate": row["success_rate"],
            "cost": row["cost"],
            "latency_ms": row["latency_ms"],
            "hallucination_rate": row["hallucination_rate"],
            "user_correction_rate": row["user_correction_rate"],
            "fitness": row["fitness"],
            "evaluated_at": row["evaluated_at"],
            "generation": row["generation"],
            "strategy": meta.get("strategy"),
            "variant_text": row["variant_text"],
        })
    return out


def latest_evaluation_for_lineage(lineage_id: int) -> dict | None:
    """Return the most recent evaluation row for a lineage variant, or None.

    Used to skip re-evaluating a variant already scored this run.
    """
    conn = connection()
    row = conn.execute(
        """
        SELECT id, lineage_id, sample_set, success_rate, cost, latency_ms,
               hallucination_rate, user_correction_rate, fitness, evaluated_at
        FROM evolution_evaluations
        WHERE lineage_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (lineage_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "lineage_id": row["lineage_id"],
        "sample_set": row["sample_set"],
        "success_rate": row["success_rate"],
        "cost": row["cost"],
        "latency_ms": row["latency_ms"],
        "hallucination_rate": row["hallucination_rate"],
        "user_correction_rate": row["user_correction_rate"],
        "fitness": row["fitness"],
        "evaluated_at": row["evaluated_at"],
    }


# --- Evolution loop runs + control state (Phase 18) -------------------------
# evolution_runs: one row per autonomous GP cycle. Doubles as the rolling-hour
# throttle window and the crash-recovery / round-robin log. evolution_state:
# single-row loop control (running / paused / off), persisted across restarts.


def start_evolution_run(*, target: str, generation: int, started_at: str | None = None) -> int:
    """Insert a cycle row with outcome='started'; returns its id. The loop
    finishes it via `finish_evolution_run`. A row left 'started' marks an
    interrupted cycle (crash recovery)."""
    conn = connection()
    cursor = conn.execute(
        """
        INSERT INTO evolution_runs (target, generation, calls_spent, outcome, started_at)
        VALUES (?, ?, 0, 'started', ?)
        """,
        (target, generation, started_at or now_iso()),
    )
    return int(cursor.lastrowid)


def finish_evolution_run(
    run_id: int,
    *,
    calls_spent: int,
    outcome: str,
    ended_at: str | None = None,
) -> None:
    """Patch a cycle row to its terminal outcome ('completed'|'partial'|'aborted')
    with the calls it spent and an end timestamp (the rolling-window key)."""
    conn = connection()
    conn.execute(
        "UPDATE evolution_runs SET calls_spent = ?, outcome = ?, ended_at = ? WHERE id = ?",
        (calls_spent, outcome, ended_at or now_iso(), run_id),
    )


def calls_in_last_hour(now: str | None = None) -> int:
    """Sum calls_spent over cycles that ended within the trailing hour. The
    rolling-window throttle: remaining = max_calls_per_hour - this."""
    cutoff_dt = _now() - timedelta(hours=1)
    cutoff = cutoff_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    conn = connection()
    row = conn.execute(
        "SELECT COALESCE(SUM(calls_spent), 0) AS n FROM evolution_runs "
        "WHERE ended_at IS NOT NULL AND ended_at >= ?",
        (cutoff,),
    ).fetchone()
    return int(row["n"]) if row and row["n"] is not None else 0


def seconds_since_last_cycle() -> float | None:
    """Seconds since the most recent cycle ended (any target), or None if no
    cycle has ever completed. Drives the `evolution.cron` interval pacing."""
    conn = connection()
    row = conn.execute(
        "SELECT MAX(ended_at) AS t FROM evolution_runs WHERE ended_at IS NOT NULL"
    ).fetchone()
    if not row or row["t"] is None:
        return None
    last = _parse_iso(row["t"])
    return (_now() - last).total_seconds()


def last_cycle_at(target: str) -> str | None:
    """Most recent completed-cycle end time for a target, or None if it has
    never run a cycle. Used by staleness-based target selection."""
    conn = connection()
    row = conn.execute(
        "SELECT MAX(ended_at) AS t FROM evolution_runs "
        "WHERE target = ? AND ended_at IS NOT NULL",
        (target,),
    ).fetchone()
    return row["t"] if row and row["t"] is not None else None


def interrupted_evolution_runs() -> list[dict]:
    """Cycles still marked 'started' (no terminal outcome) — interrupted by a
    crash. The loop reconciles these on restart."""
    conn = connection()
    rows = conn.execute(
        "SELECT id, target, generation, started_at FROM evolution_runs "
        "WHERE outcome = 'started' ORDER BY id"
    ).fetchall()
    return [
        {"id": r["id"], "target": r["target"], "generation": r["generation"],
         "started_at": r["started_at"]}
        for r in rows
    ]


def evolution_runs_recent(n: int = 10) -> list[dict]:
    """Most recent cycles, newest first — for `/evolution status`."""
    if n <= 0:
        return []
    conn = connection()
    rows = conn.execute(
        "SELECT id, target, generation, calls_spent, outcome, started_at, ended_at "
        "FROM evolution_runs ORDER BY id DESC LIMIT ?",
        (n,),
    ).fetchall()
    return [
        {"id": r["id"], "target": r["target"], "generation": r["generation"],
         "calls_spent": r["calls_spent"], "outcome": r["outcome"],
         "started_at": r["started_at"], "ended_at": r["ended_at"]}
        for r in rows
    ]


def get_evolution_status() -> str:
    """Return the loop control status; defaults to 'paused' when unset so the
    loop never auto-spends on first launch."""
    conn = connection()
    row = conn.execute("SELECT status FROM evolution_state WHERE id = 1").fetchone()
    return row["status"] if row else "paused"


def set_evolution_status(status: str) -> None:
    """Upsert the single-row loop control state."""
    if status not in ("running", "paused", "off"):
        raise ValueError(f"invalid evolution status: {status}")
    conn = connection()
    conn.execute(
        """
        INSERT INTO evolution_state (id, status, updated_at) VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at
        """,
        (status, now_iso()),
    )


# --- Promotions: pending queue + active swap (Phase 19) ---------------------
# pending_promotions: the loop proposes here when a champion beats the active
# baseline; the user approves/rejects via /improvements. active_evolutions: the
# single promoted variant per target, consulted by the live read paths
# (build_system_prompt, router, repair) for the live swap.


def append_pending_promotion(*, target: str, lineage_id: int, proposed_at: str | None = None) -> int:
    """Enqueue a promotion proposal; returns its id."""
    conn = connection()
    cursor = conn.execute(
        """
        INSERT INTO pending_promotions (lineage_id, target, proposed_at)
        VALUES (?, ?, ?)
        """,
        (lineage_id, target, proposed_at or now_iso()),
    )
    return int(cursor.lastrowid)


def has_open_promotion(target: str) -> bool:
    """True if `target` already has an undecided promotion (the proposer skips
    re-proposing while one is pending)."""
    conn = connection()
    row = conn.execute(
        "SELECT 1 FROM pending_promotions WHERE target = ? AND decided_at IS NULL LIMIT 1",
        (target,),
    ).fetchone()
    return row is not None


def open_pending_promotions() -> list[dict]:
    """Undecided promotions, oldest first, joined to their lineage variant
    (target, lineage_id, variant_text, strategy, generation). Powers
    `/improvements`."""
    import json as _json

    conn = connection()
    rows = conn.execute(
        """
        SELECT p.id, p.target, p.lineage_id, p.proposed_at,
               l.variant_text, l.variant_metadata, l.generation
        FROM pending_promotions p
        JOIN evolution_lineage l ON l.id = p.lineage_id
        WHERE p.decided_at IS NULL
        ORDER BY p.id
        """,
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        try:
            meta = _json.loads(r["variant_metadata"]) if r["variant_metadata"] else {}
        except Exception:
            meta = {}
        out.append({
            "id": r["id"], "target": r["target"], "lineage_id": r["lineage_id"],
            "proposed_at": r["proposed_at"], "variant_text": r["variant_text"],
            "strategy": meta.get("strategy"), "kind": meta.get("kind", "prompt"),
            "generation": r["generation"],
        })
    return out


def get_pending_promotion(promotion_id: int) -> dict | None:
    conn = connection()
    r = conn.execute(
        "SELECT id, target, lineage_id, proposed_at, decided_at, decision "
        "FROM pending_promotions WHERE id = ?",
        (promotion_id,),
    ).fetchone()
    if r is None:
        return None
    return {"id": r["id"], "target": r["target"], "lineage_id": r["lineage_id"],
            "proposed_at": r["proposed_at"], "decided_at": r["decided_at"],
            "decision": r["decision"]}


def decide_promotion(promotion_id: int, decision: str, *, decided_at: str | None = None) -> None:
    """Stamp a pending promotion as approved/rejected. Idempotent-safe: only
    patches rows still undecided."""
    if decision not in ("approved", "rejected"):
        raise ValueError(f"invalid decision: {decision}")
    conn = connection()
    conn.execute(
        "UPDATE pending_promotions SET decision = ?, decided_at = ? "
        "WHERE id = ? AND decided_at IS NULL",
        (decision, decided_at or now_iso(), promotion_id),
    )


def set_active_evolution(target: str, lineage_id: int, *, promoted_at: str | None = None) -> None:
    """Promote a variant: upsert the single active row for the target. The live
    read paths consult this for the swap."""
    conn = connection()
    conn.execute(
        """
        INSERT INTO active_evolutions (target, lineage_id, promoted_at) VALUES (?, ?, ?)
        ON CONFLICT(target) DO UPDATE SET lineage_id = excluded.lineage_id,
                                          promoted_at = excluded.promoted_at
        """,
        (target, lineage_id, promoted_at or now_iso()),
    )


def clear_active_evolution(target: str) -> bool:
    """Roll back a promotion: remove the active row (revert to file/default).
    Returns True if a row was removed."""
    conn = connection()
    cur = conn.execute("DELETE FROM active_evolutions WHERE target = ?", (target,))
    return cur.rowcount > 0


def active_evolution(target: str) -> dict | None:
    """The active promoted variant for a target (id + variant_text), or None."""
    conn = connection()
    r = conn.execute(
        """
        SELECT a.lineage_id, a.promoted_at, l.variant_text, l.generation
        FROM active_evolutions a JOIN evolution_lineage l ON l.id = a.lineage_id
        WHERE a.target = ?
        """,
        (target,),
    ).fetchone()
    if r is None:
        return None
    return {"lineage_id": r["lineage_id"], "promoted_at": r["promoted_at"],
            "variant_text": r["variant_text"], "generation": r["generation"]}


