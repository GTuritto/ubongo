"""Grant registry state (v0.5 phase 05): persistent capability grants.

A grant is human consent for a capability class (e.g. `connector:compendium`)
that survives the turn. The first connector turn touching a class with no active
grant asks once through the approval seam; approving writes a row here, so later
turns auto-proceed; revoking re-arms the ask. Pure CRUD over store.connection();
the grant-check policy lives in `governance/grants.py`, the management surface in
the REPL / CLI. Written by the orchestrator on approval — the same governance
carve-out as governance_decisions, not the Memory Agent.
"""

from __future__ import annotations

from ubongo.memory.store import connection, now_iso


def grant(
    capability_class: str,
    *,
    consequence_class: str = "irreversible",
    scope: str = "*",
    purpose: str | None = None,
) -> int:
    """Persist an active grant for a capability class. Returns the row id.
    Idempotent at the policy layer (callers check `is_granted` first); a second
    call simply adds another active row, which `is_granted` still honors."""
    cur = connection().execute(
        "INSERT INTO grants "
        "(capability_class, consequence_class, scope, purpose, status, created_at) "
        "VALUES (?, ?, ?, ?, 'active', ?)",
        (capability_class, consequence_class, scope, purpose, now_iso()),
    )
    return int(cur.lastrowid)


def is_granted(capability_class: str, *, scope: str = "*") -> bool:
    """True when an active grant covers this capability class. A `*`-scoped
    grant covers any agent; an agent-scoped grant covers that agent or `*`."""
    row = connection().execute(
        "SELECT 1 FROM grants WHERE capability_class = ? AND status = 'active' "
        "AND scope IN ('*', ?) LIMIT 1",
        (capability_class, scope),
    ).fetchone()
    return row is not None


def active_grants() -> list[dict]:
    """Every active grant, newest first — the `/grants` surface."""
    rows = connection().execute(
        "SELECT id, capability_class, consequence_class, scope, purpose, "
        "status, created_at, revoked_at FROM grants "
        "WHERE status = 'active' ORDER BY id DESC",
    ).fetchall()
    return [dict(r) for r in rows]


def get_grant(grant_id: int) -> dict | None:
    row = connection().execute(
        "SELECT id, capability_class, consequence_class, scope, purpose, "
        "status, created_at, revoked_at FROM grants WHERE id = ?",
        (grant_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def revoke(grant_id: int) -> bool:
    """Revoke an active grant. Returns True if it was active and got revoked,
    False if it was absent or already revoked (idempotency guard)."""
    cur = connection().execute(
        "UPDATE grants SET status = 'revoked', revoked_at = ? "
        "WHERE id = ? AND status = 'active'",
        (now_iso(), grant_id),
    )
    return cur.rowcount > 0
