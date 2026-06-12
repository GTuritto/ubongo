from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ubongo.config import load_config
from ubongo import events

logger = logging.getLogger("ubongo.memory.store")


@dataclass(frozen=True)
class Conversation:
    id: int
    started_at: str
    ended_at: str | None
    active_persona: str | None


@dataclass(frozen=True)
class Message:
    id: int
    conversation_id: int
    role: str
    content: str
    timestamp: str
    persona: str | None
    model: str | None
    tokens_in: int
    tokens_out: int


@dataclass(frozen=True)
class Summary:
    id: int
    conversation_id: int
    covers_from_message_id: int
    covers_to_message_id: int
    content: str
    strategy: str
    created_at: str


@dataclass(frozen=True)
class Session:
    user_id: int
    last_message_at: str | None
    active_persona: str | None
    override_until: str | None
    current_conversation_id: int | None
    auto_mode: bool


@dataclass(frozen=True)
class RecallContext:
    summary_text: str | None
    messages: list[Message]
    # Phase 20: messages retrieved by semantic similarity to the current query,
    # outside the recency window. Empty when no query / embeddings unavailable.
    semantic_messages: list[Message] = field(default_factory=list)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DB_PATH = _REPO_ROOT / "data" / "ubongo.db"
_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

_connection: sqlite3.Connection | None = None
_bootstrapped = False


def get_db_path() -> Path:
    return _DB_PATH


def set_db_path(path: Path) -> None:
    """Override the DB path (used by tests with tempfiles)."""
    global _DB_PATH, _connection, _bootstrapped
    _DB_PATH = path
    if _connection is not None:
        _connection.close()
    _connection = None
    _bootstrapped = False
    # Phase 20: the embeddings layer caches sqlite-vec readiness per connection;
    # forget it when the DB changes. Lazy import avoids a load-time cycle.
    try:
        from ubongo.memory import embeddings
        embeddings.reset()
    except Exception:
        pass


def _now() -> datetime:
    fake = os.environ.get("UBONGO_FAKE_NOW")
    if fake:
        return datetime.fromisoformat(fake)
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return _now().isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ensure_dir() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def bootstrap() -> sqlite3.Connection:
    global _connection, _bootstrapped
    if _connection is None:
        _ensure_dir()
        # check_same_thread=False: Phase 12a put agents in asyncio.to_thread
        # workers; the singleton connection is now read from threads other
        # than the one that created it (e.g., ResearchAgent.run reading
        # store.last_n_messages_global from a parallel-mode worker thread).
        # The store is autocommit (isolation_level=None) and SQLite serializes
        # concurrent operations on a single connection internally; this is
        # safe for the read-heavy single-process workload. Phase 16+ revisits
        # if write contention becomes a concern.
        _connection = sqlite3.connect(
            _DB_PATH, isolation_level=None, check_same_thread=False,
        )
        _connection.row_factory = sqlite3.Row
        _connection.execute("PRAGMA foreign_keys = ON")
    if not _bootstrapped:
        schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        _connection.executescript(schema_sql)
        _migrate_workflow_runs_in_progress(_connection)
        _migrate_agent_runs_retried_column(_connection)
        _bootstrapped = True
    return _connection


def _migrate_workflow_runs_in_progress(conn: sqlite3.Connection) -> None:
    """Phase 9e: workflow_runs.outcome CHECK gained 'in_progress'. Existing DBs
    created under the prior schema keep the old constraint until we rebuild
    the table. Detects the old constraint and rewrites if needed.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='workflow_runs'"
    ).fetchone()
    if row is None or "in_progress" in (row["sql"] or ""):
        return
    conn.executescript(
        """
        ALTER TABLE workflow_runs RENAME TO workflow_runs_old;
        CREATE TABLE workflow_runs (
          id INTEGER PRIMARY KEY,
          conversation_id INTEGER NOT NULL,
          message_id INTEGER NOT NULL,
          classification JSON NOT NULL,
          workflow JSON NOT NULL,
          execution_mode TEXT NOT NULL,
          started_at TIMESTAMP NOT NULL,
          ended_at TIMESTAMP,
          outcome TEXT NOT NULL CHECK (outcome IN ('in_progress', 'success', 'failure', 'repaired'))
        );
        INSERT INTO workflow_runs
            (id, conversation_id, message_id, classification, workflow,
             execution_mode, started_at, ended_at, outcome)
        SELECT id, conversation_id, message_id, classification, workflow,
             execution_mode, started_at, ended_at, outcome
        FROM workflow_runs_old;
        DROP TABLE workflow_runs_old;
        CREATE INDEX IF NOT EXISTS idx_workflow_runs_conv ON workflow_runs(conversation_id);
        """
    )


def _migrate_agent_runs_retried_column(conn: sqlite3.Connection) -> None:
    """Phase 11d: agent_runs gained a `retried INTEGER NOT NULL DEFAULT 0`
    column. CREATE TABLE IF NOT EXISTS is a no-op on existing DBs, so add
    the column when missing."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(agent_runs)")}
    if "retried" in cols:
        return
    conn.execute(
        "ALTER TABLE agent_runs ADD COLUMN retried INTEGER NOT NULL DEFAULT 0"
    )


def connection() -> sqlite3.Connection:
    return bootstrap()


def is_connected() -> bool:
    """True if a DB connection exists in this process. Lets read paths (e.g. the
    Phase 19 live swap in build_system_prompt) skip a promotion lookup in
    processes that never opened the DB, rather than bootstrapping one as a side
    effect of pure prompt assembly."""
    return _connection is not None


# --- conversations ---


def start_conversation(active_persona: str) -> int:
    conn = connection()
    cursor = conn.execute(
        "INSERT INTO conversations (started_at, active_persona) VALUES (?, ?)",
        (now_iso(), active_persona),
    )
    return int(cursor.lastrowid)


def end_conversation(conversation_id: int, when: str | None = None) -> None:
    conn = connection()
    conn.execute(
        "UPDATE conversations SET ended_at = ? WHERE id = ?",
        (when or now_iso(), conversation_id),
    )


def get_conversation(conversation_id: int) -> Conversation | None:
    conn = connection()
    row = conn.execute(
        "SELECT id, started_at, ended_at, active_persona FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    if row is None:
        return None
    return Conversation(
        id=row["id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        active_persona=row["active_persona"],
    )


# --- messages ---


def append_message(
    conversation_id: int,
    role: str,
    content: str,
    *,
    persona: str | None = None,
    model: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> int:
    conn = connection()
    cursor = conn.execute(
        """
        INSERT INTO messages
            (conversation_id, role, content, timestamp, persona, model, tokens_in, tokens_out)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (conversation_id, role, content, now_iso(), persona, model, tokens_in, tokens_out),
    )
    message_id = int(cursor.lastrowid)
    # Phase 20: index the message for semantic recall (best-effort, idempotent).
    # This is the single place every user/assistant turn is born, so it covers
    # both master's user-message write and the Memory Agent's assistant write.
    # A no-op when embeddings are disabled / sqlite-vec is unavailable; never
    # raises, so the message write (and the turn) never depend on the embedding
    # endpoint. Lazy import avoids a load-time cycle.
    try:
        from ubongo.memory import embeddings
        embeddings.index_message(message_id, content)
    except Exception:
        pass
    return message_id


def _row_to_message(row: sqlite3.Row) -> Message:
    return Message(
        id=row["id"],
        conversation_id=row["conversation_id"],
        role=row["role"],
        content=row["content"],
        timestamp=row["timestamp"],
        persona=row["persona"],
        model=row["model"],
        tokens_in=row["tokens_in"] or 0,
        tokens_out=row["tokens_out"] or 0,
    )


def last_n_messages(conversation_id: int, n: int) -> list[Message]:
    conn = connection()
    rows = conn.execute(
        """
        SELECT * FROM (
            SELECT id, conversation_id, role, content, timestamp, persona, model, tokens_in, tokens_out
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id DESC
            LIMIT ?
        ) ORDER BY id ASC
        """,
        (conversation_id, n),
    ).fetchall()
    return [_row_to_message(r) for r in rows]


def last_n_messages_global(n: int) -> list[Message]:
    """Return the last N messages across ALL conversations, oldest first.

    Phase-9 helper used by the Research Agent for cross-session retrieval.
    Phase 20 will replace with sqlite-vec semantic recall.
    """
    if n <= 0:
        return []
    conn = connection()
    rows = conn.execute(
        """
        SELECT * FROM (
            SELECT id, conversation_id, role, content, timestamp, persona, model, tokens_in, tokens_out
            FROM messages
            ORDER BY id DESC
            LIMIT ?
        ) ORDER BY id ASC
        """,
        (n,),
    ).fetchall()
    return [_row_to_message(r) for r in rows]


def messages_in_range(conversation_id: int, from_id: int, to_id: int) -> list[Message]:
    conn = connection()
    rows = conn.execute(
        """
        SELECT id, conversation_id, role, content, timestamp, persona, model, tokens_in, tokens_out
        FROM messages
        WHERE conversation_id = ? AND id >= ? AND id <= ?
        ORDER BY id ASC
        """,
        (conversation_id, from_id, to_id),
    ).fetchall()
    return [_row_to_message(r) for r in rows]


def max_message_id(conversation_id: int) -> int:
    conn = connection()
    row = conn.execute(
        "SELECT COALESCE(MAX(id), 0) AS m FROM messages WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    return int(row["m"]) if row else 0


# --- summaries ---


def _row_to_summary(row: sqlite3.Row) -> Summary:
    return Summary(
        id=row["id"],
        conversation_id=row["conversation_id"],
        covers_from_message_id=row["covers_from_message_id"],
        covers_to_message_id=row["covers_to_message_id"],
        content=row["content"],
        strategy=row["strategy"],
        created_at=row["created_at"],
    )


def latest_summary(conversation_id: int) -> Summary | None:
    conn = connection()
    row = conn.execute(
        """
        SELECT id, conversation_id, covers_from_message_id, covers_to_message_id, content, strategy, created_at
        FROM summaries
        WHERE conversation_id = ?
        ORDER BY covers_to_message_id DESC, id DESC
        LIMIT 1
        """,
        (conversation_id,),
    ).fetchone()
    return _row_to_summary(row) if row else None


def latest_summary_from_other_conversations(exclude_conversation_id: int) -> Summary | None:
    """Return the most recent summary written for any conversation other than
    the one specified. Used as cross-session memory: when a fresh conversation
    has no summary yet, we inherit the prior conversation's summary so durable
    facts (birthday, preferences, project context) survive the timeout."""
    conn = connection()
    row = conn.execute(
        """
        SELECT id, conversation_id, covers_from_message_id, covers_to_message_id, content, strategy, created_at
        FROM summaries
        WHERE conversation_id != ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (exclude_conversation_id,),
    ).fetchone()
    return _row_to_summary(row) if row else None


def persist_summary(
    conversation_id: int,
    covers_from_message_id: int,
    covers_to_message_id: int,
    content: str,
    strategy: str,
) -> int:
    conn = connection()
    cursor = conn.execute(
        """
        INSERT INTO summaries
            (conversation_id, covers_from_message_id, covers_to_message_id, content, strategy, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (conversation_id, covers_from_message_id, covers_to_message_id, content, strategy, now_iso()),
    )
    return int(cursor.lastrowid)


def count_messages_since_summary(conversation_id: int) -> int:
    last = latest_summary(conversation_id)
    floor = last.covers_to_message_id if last else 0
    conn = connection()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM messages WHERE conversation_id = ? AND id > ?",
        (conversation_id, floor),
    ).fetchone()
    return int(row["c"]) if row else 0


# --- sessions ---


def get_session(user_id: int = 1) -> Session | None:
    conn = connection()
    row = conn.execute(
        """
        SELECT user_id, last_message_at, active_persona, override_until, current_conversation_id, auto_mode
        FROM sessions WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    return Session(
        user_id=row["user_id"],
        last_message_at=row["last_message_at"],
        active_persona=row["active_persona"],
        override_until=row["override_until"],
        current_conversation_id=row["current_conversation_id"],
        auto_mode=bool(row["auto_mode"]),
    )


def upsert_session(
    user_id: int = 1,
    *,
    last_message_at: str | None = None,
    active_persona: str | None = None,
    current_conversation_id: int | None = None,
    auto_mode: bool | None = None,
) -> None:
    """Insert or partial-update a session row. Unspecified fields are preserved on update."""
    existing = get_session(user_id)
    conn = connection()
    if existing is None:
        conn.execute(
            """
            INSERT INTO sessions
                (user_id, last_message_at, active_persona, override_until,
                 current_conversation_id, auto_mode)
            VALUES (?, ?, ?, NULL, ?, ?)
            """,
            (
                user_id,
                last_message_at,
                active_persona,
                current_conversation_id,
                int(bool(auto_mode)) if auto_mode is not None else 0,
            ),
        )
        return
    new_last = last_message_at if last_message_at is not None else existing.last_message_at
    new_persona = active_persona if active_persona is not None else existing.active_persona
    new_conv_id = (
        current_conversation_id
        if current_conversation_id is not None
        else existing.current_conversation_id
    )
    new_auto = int(bool(auto_mode)) if auto_mode is not None else int(existing.auto_mode)
    conn.execute(
        """
        UPDATE sessions
        SET last_message_at = ?, active_persona = ?, current_conversation_id = ?, auto_mode = ?
        WHERE user_id = ?
        """,
        (new_last, new_persona, new_conv_id, new_auto, user_id),
    )


# --- session timeout / current-or-new conversation ---


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _session_timeout() -> timedelta:
    config = load_config()
    minutes = config.get("memory", {}).get("session_timeout_minutes", 30)
    try:
        return timedelta(minutes=int(minutes))
    except (TypeError, ValueError):
        return timedelta(minutes=30)


def current_or_new_conversation(persona: str, user_id: int = 1) -> int:
    """Return the active conversation id for the user, starting a new one if needed.

    The current conversation continues if (now - last_message_at) is within the
    session timeout. Otherwise the previous conversation is closed (ended_at set
    to the previous last_message_at) and a new conversation starts.
    """
    now = _now()
    session = get_session(user_id)

    if session and session.current_conversation_id and session.last_message_at:
        last = _parse_iso(session.last_message_at)
        if now - last < _session_timeout():
            return session.current_conversation_id
        # Timeout exceeded; close the previous conversation.
        end_conversation(session.current_conversation_id, when=session.last_message_at)

    new_id = start_conversation(persona)
    upsert_session(
        user_id=user_id,
        active_persona=persona,
        current_conversation_id=new_id,
        last_message_at=now_iso(),
    )
    return new_id


# --- recall ---


def recall(conversation_id: int, query: str | None = None) -> RecallContext:
    config = load_config()
    mem_cfg = config.get("memory", {})
    recall_turns = int(mem_cfg.get("recall_turns", 10))

    summary = latest_summary(conversation_id)
    inherited = False
    if summary is None:
        cross = latest_summary_from_other_conversations(exclude_conversation_id=conversation_id)
        if cross is not None:
            summary = cross
            inherited = True

    messages = last_n_messages(conversation_id, recall_turns)

    # Phase 20: semantic recall. When a query is given and embeddings are
    # available, retrieve the most similar prior messages in this conversation
    # that are NOT already in the recency window. Best-effort: any failure or a
    # disabled/unavailable embeddings layer leaves this empty (recency-only).
    semantic: list[Message] = []
    if query:
        try:
            from ubongo.memory import embeddings
            top_k = int((mem_cfg.get("embeddings", {}) or {}).get("recall_top_k", 5))
            recency_ids = {m.id for m in messages}
            hits = embeddings.search_messages(
                query, top_k, exclude_ids=recency_ids, conversation_id=conversation_id
            )
            semantic = messages_by_ids([mid for mid, _ in hits])
        except Exception:
            semantic = []

    events.dispatch(
        "after_recall",
        {
            "conversation_id": conversation_id,
            "messages_since_summary": count_messages_since_summary(conversation_id),
            "recall_turns": recall_turns,
            "summary_inherited": inherited,
            "semantic_hits": len(semantic),
        },
    )

    return RecallContext(
        summary_text=summary.content if summary else None,
        messages=messages,
        semantic_messages=semantic,
    )


# --- Evolution lineage (Phase 16) -------------------------------------------
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


# --- Embedding meta + vault links (Phase 20) --------------------------------
# embedding_meta is the idempotency sidecar for message vectors (the vectors
# live in vec_messages, created lazily by memory/embeddings.py). vault_links is
# the [[wikilink]] graph populated from daily notes.


def embedding_meta_hash(message_id: int) -> str | None:
    """The stored text hash for a message's embedding, or None if not indexed."""
    conn = connection()
    row = conn.execute(
        "SELECT text_hash FROM embedding_meta WHERE message_id = ?", (message_id,)
    ).fetchone()
    return row["text_hash"] if row else None


def upsert_embedding_meta(message_id: int, text_hash: str) -> None:
    conn = connection()
    conn.execute(
        """
        INSERT INTO embedding_meta (message_id, text_hash, embedded_at) VALUES (?, ?, ?)
        ON CONFLICT(message_id) DO UPDATE SET text_hash = excluded.text_hash,
                                              embedded_at = excluded.embedded_at
        """,
        (message_id, text_hash, now_iso()),
    )


def messages_by_ids(ids: list[int]) -> list["Message"]:
    """Fetch messages by id, returned in ascending id order. Used to hydrate
    semantic-recall hits into Message rows."""
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    conn = connection()
    rows = conn.execute(
        f"""
        SELECT id, conversation_id, role, content, timestamp, persona, model, tokens_in, tokens_out
        FROM messages WHERE id IN ({placeholders}) ORDER BY id ASC
        """,
        ids,
    ).fetchall()
    return [_row_to_message(r) for r in rows]


def upsert_vault_link(source_path: str, target_path: str, *, link_type: str = "wikilink") -> None:
    """Idempotent insert of a vault link (composite PK absorbs duplicates)."""
    conn = connection()
    conn.execute(
        """
        INSERT INTO vault_links (source_path, target_path, link_type, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(source_path, target_path, link_type) DO NOTHING
        """,
        (source_path, target_path, link_type, now_iso()),
    )


def vault_links_from(source_path: str) -> list[str]:
    """Outbound link targets from a note."""
    conn = connection()
    rows = conn.execute(
        "SELECT target_path FROM vault_links WHERE source_path = ? ORDER BY target_path",
        (source_path,),
    ).fetchall()
    return [r["target_path"] for r in rows]


def vault_links_to(target_path: str) -> list[str]:
    """Inbound sources linking to a note (backlinks)."""
    conn = connection()
    rows = conn.execute(
        "SELECT source_path FROM vault_links WHERE target_path = ? ORDER BY source_path",
        (target_path,),
    ).fetchall()
    return [r["source_path"] for r in rows]


# --- Vault sync: state + conflicts (Phase 21) -------------------------------
# vault_state records the hash of what the system last wrote to each note (echo
# suppression for the watcher). vault_conflicts queues edit/write collisions.


def record_vault_write(path: str, content_hash: str) -> None:
    """Record the hash the system just wrote to `path` (vault-relative)."""
    conn = connection()
    conn.execute(
        """
        INSERT INTO vault_state (path, content_hash, last_written_at) VALUES (?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET content_hash = excluded.content_hash,
                                        last_written_at = excluded.last_written_at
        """,
        (path, content_hash, now_iso()),
    )


def get_vault_hash(path: str) -> str | None:
    conn = connection()
    row = conn.execute("SELECT content_hash FROM vault_state WHERE path = ?", (path,)).fetchone()
    return row["content_hash"] if row else None


def append_vault_conflict(*, path: str, system_hash: str | None, disk_hash: str | None) -> int:
    """Queue an edit/write collision; returns its id. De-dups: if an open
    conflict already exists for the path with the same disk_hash, reuse it."""
    conn = connection()
    existing = conn.execute(
        "SELECT id FROM vault_conflicts WHERE path = ? AND status = 'open' AND disk_hash IS ? LIMIT 1",
        (path, disk_hash),
    ).fetchone()
    if existing:
        return int(existing["id"])
    cursor = conn.execute(
        """
        INSERT INTO vault_conflicts (path, detected_at, system_hash, disk_hash, status)
        VALUES (?, ?, ?, ?, 'open')
        """,
        (path, now_iso(), system_hash, disk_hash),
    )
    return int(cursor.lastrowid)


def open_vault_conflicts() -> list[dict]:
    conn = connection()
    rows = conn.execute(
        "SELECT id, path, detected_at, system_hash, disk_hash FROM vault_conflicts "
        "WHERE status = 'open' ORDER BY id"
    ).fetchall()
    return [
        {"id": r["id"], "path": r["path"], "detected_at": r["detected_at"],
         "system_hash": r["system_hash"], "disk_hash": r["disk_hash"]}
        for r in rows
    ]


def get_vault_conflict(conflict_id: int) -> dict | None:
    conn = connection()
    r = conn.execute(
        "SELECT id, path, detected_at, system_hash, disk_hash, status, resolution "
        "FROM vault_conflicts WHERE id = ?", (conflict_id,)
    ).fetchone()
    if r is None:
        return None
    return {"id": r["id"], "path": r["path"], "detected_at": r["detected_at"],
            "system_hash": r["system_hash"], "disk_hash": r["disk_hash"],
            "status": r["status"], "resolution": r["resolution"]}


def resolve_vault_conflict(conflict_id: int, resolution: str) -> bool:
    """Mark an open conflict resolved with the chosen resolution. Returns True if
    a still-open row was updated."""
    conn = connection()
    cur = conn.execute(
        "UPDATE vault_conflicts SET status = 'resolved', resolution = ? "
        "WHERE id = ? AND status = 'open'",
        (resolution, conflict_id),
    )
    return cur.rowcount > 0


# --- Authored skills (self-extension experiment) ----------------------------
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


