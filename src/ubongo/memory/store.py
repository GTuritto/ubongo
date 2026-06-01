from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
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
    return int(cursor.lastrowid)


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


def recall(conversation_id: int) -> RecallContext:
    config = load_config()
    recall_turns = int(config.get("memory", {}).get("recall_turns", 10))

    summary = latest_summary(conversation_id)
    inherited = False
    if summary is None:
        cross = latest_summary_from_other_conversations(exclude_conversation_id=conversation_id)
        if cross is not None:
            summary = cross
            inherited = True

    messages = last_n_messages(conversation_id, recall_turns)

    events.dispatch(
        "after_recall",
        {
            "conversation_id": conversation_id,
            "messages_since_summary": count_messages_since_summary(conversation_id),
            "recall_turns": recall_turns,
            "summary_inherited": inherited,
        },
    )

    return RecallContext(
        summary_text=summary.content if summary else None,
        messages=messages,
    )


# --- workflow_runs + governance_decisions ---


def append_workflow_run(
    conversation_id: int,
    message_id: int,
    classification: dict,
    workflow: dict,
    execution_mode: str,
    outcome: str,
    started_at: str,
    ended_at: str | None = None,
) -> int:
    import json as _json

    conn = connection()
    cursor = conn.execute(
        """
        INSERT INTO workflow_runs
            (conversation_id, message_id, classification, workflow,
             execution_mode, started_at, ended_at, outcome)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            conversation_id,
            message_id,
            _json.dumps(classification),
            _json.dumps(workflow),
            execution_mode,
            started_at,
            ended_at,
            outcome,
        ),
    )
    return int(cursor.lastrowid)


def update_workflow_run_outcome(
    workflow_run_id: int,
    *,
    outcome: str,
    ended_at: str | None = None,
) -> None:
    """Patch outcome (and optionally ended_at) on a workflow_runs row.

    Phase 9e: workflows are INSERTed with outcome='in_progress' before the
    runner dispatches agents, then UPDATEd to success/failure when done.
    """
    conn = connection()
    conn.execute(
        "UPDATE workflow_runs SET outcome = ?, ended_at = COALESCE(?, ended_at) WHERE id = ?",
        (outcome, ended_at, workflow_run_id),
    )


def append_agent_run(
    workflow_run_id: int,
    *,
    agent: str,
    model: str | None,
    input: dict,
    output: dict,
    confidence: float | None,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    outcome: str,
    started_at: str,
    ended_at: str,
    retried: bool = False,
) -> int:
    """Persist one agent_runs row. Called by the WorkflowRunner per agent dispatch.

    Phase 11d: `retried=True` marks the row as the second attempt at the
    same agent (Repair Agent's single-retry path). The trace renderer
    surfaces this so the operator can tell first attempt from retry.
    """
    import json as _json

    conn = connection()
    cursor = conn.execute(
        """
        INSERT INTO agent_runs
            (workflow_run_id, agent, model, input, output, confidence,
             tokens_in, tokens_out, latency_ms, outcome, started_at, ended_at,
             retried)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workflow_run_id,
            agent,
            model,
            _json.dumps(input),
            _json.dumps(output),
            confidence,
            tokens_in,
            tokens_out,
            latency_ms,
            outcome,
            started_at,
            ended_at,
            1 if retried else 0,
        ),
    )
    return int(cursor.lastrowid)


def append_governance_decision(
    workflow_run_id: int,
    *,
    intent: str | None,
    risk: str | None,
    confidence: float | None,
    reversibility: str | None,
    action: str,
    approval_response: str | None = None,
    decided_at: str | None = None,
) -> int:
    conn = connection()
    cursor = conn.execute(
        """
        INSERT INTO governance_decisions
            (workflow_run_id, intent, risk, confidence, reversibility,
             action, approval_response, decided_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workflow_run_id,
            intent,
            risk,
            confidence,
            reversibility,
            action,
            approval_response,
            decided_at or now_iso(),
        ),
    )
    return int(cursor.lastrowid)


def update_governance_decision(decision_id: int, approval_response: str) -> None:
    """Phase 15b: persist the user's y/n approval onto a governance_decisions
    row written earlier in the turn with approval_response=NULL.

    The row is INSERTed synchronously during master.handle; the interactive
    approval prompt happens after handle() returns, so the response is patched
    in by a second call from the REPL.
    """
    connection().execute(
        "UPDATE governance_decisions SET approval_response = ? WHERE id = ?",
        (approval_response, decision_id),
    )


def append_repair_run(
    workflow_run_id: int,
    *,
    agent: str,
    failure_kind: str,
    original_error: str | None,
    strategy_attempted: str,
    peer_agent: str | None,
    override_model: str | None,
    attempt_index: int,
    outcome: str,
    started_at: str,
    ended_at: str | None,
) -> int:
    """Persist one repair_runs row (Phase 13e). Called by the WorkflowRunner
    after each Repair strategy attempt — recovered, failed, or aborted."""
    conn = connection()
    cursor = conn.execute(
        """
        INSERT INTO repair_runs
            (workflow_run_id, agent, failure_kind, original_error,
             strategy_attempted, peer_agent, override_model,
             attempt_index, outcome, started_at, ended_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workflow_run_id,
            agent,
            failure_kind,
            original_error,
            strategy_attempted,
            peer_agent,
            override_model,
            attempt_index,
            outcome,
            started_at,
            ended_at,
        ),
    )
    return int(cursor.lastrowid)


def repair_runs_for_workflow(workflow_run_id: int) -> list[dict]:
    """Return all repair_runs rows for a workflow_run, in attempt order.

    Each dict carries: id, agent, failure_kind, original_error,
    strategy_attempted, peer_agent, override_model, attempt_index, outcome,
    started_at, ended_at.
    """
    conn = connection()
    rows = conn.execute(
        """
        SELECT id, agent, failure_kind, original_error, strategy_attempted,
               peer_agent, override_model, attempt_index, outcome,
               started_at, ended_at
        FROM repair_runs
        WHERE workflow_run_id = ?
        ORDER BY id
        """,
        (workflow_run_id,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "agent": r["agent"],
            "failure_kind": r["failure_kind"],
            "original_error": r["original_error"],
            "strategy_attempted": r["strategy_attempted"],
            "peer_agent": r["peer_agent"],
            "override_model": r["override_model"],
            "attempt_index": r["attempt_index"],
            "outcome": r["outcome"],
            "started_at": r["started_at"],
            "ended_at": r["ended_at"],
        }
        for r in rows
    ]


def last_n_governance_decisions(n: int = 10) -> list[dict]:
    """Return the last N decisions joined with their workflow_runs for display.

    Each dict carries: id, decided_at, intent, risk, confidence, action,
    persona (extracted from workflow JSON), execution_mode, workflow_run_id.
    """
    import json as _json

    if n <= 0:
        return []
    conn = connection()
    rows = conn.execute(
        """
        SELECT g.id, g.decided_at, g.intent, g.risk, g.confidence, g.action,
               g.reversibility, g.workflow_run_id, w.execution_mode, w.workflow
        FROM governance_decisions g
        JOIN workflow_runs w ON w.id = g.workflow_run_id
        ORDER BY g.decided_at DESC, g.id DESC
        LIMIT ?
        """,
        (n,),
    ).fetchall()
    out: list[dict] = []
    for row in rows:
        persona = None
        try:
            wf = _json.loads(row["workflow"]) if row["workflow"] else {}
            persona = wf.get("persona")
        except Exception:
            persona = None
        out.append({
            "id": row["id"],
            "decided_at": row["decided_at"],
            "intent": row["intent"],
            "risk": row["risk"],
            "confidence": row["confidence"],
            "reversibility": row["reversibility"],
            "action": row["action"],
            "workflow_run_id": row["workflow_run_id"],
            "execution_mode": row["execution_mode"],
            "persona": persona,
        })
    return out


def last_n_workflow_runs(n: int = 1) -> list[dict]:
    """Return the last N workflow_runs joined with their agent_runs and the
    governance_decision for each. Used by the /trace REPL command (Phase 10).

    Each dict carries:
      id, conversation_id, message_id, classification (parsed JSON),
      workflow (parsed JSON), execution_mode, outcome, started_at, ended_at,
      agent_runs: list of {agent, model, confidence, tokens_in, tokens_out,
                            latency_ms, outcome, started_at, ended_at, error},
      governance: {id, action, reason, confidence, intent, risk} | None
    """
    import json as _json

    if n <= 0:
        return []
    conn = connection()
    wf_rows = conn.execute(
        """
        SELECT id, conversation_id, message_id, classification, workflow,
               execution_mode, started_at, ended_at, outcome
        FROM workflow_runs
        ORDER BY id DESC
        LIMIT ?
        """,
        (n,),
    ).fetchall()
    if not wf_rows:
        return []
    wf_ids = [row["id"] for row in wf_rows]
    placeholders = ",".join("?" for _ in wf_ids)
    ar_rows = conn.execute(
        f"""
        SELECT id, workflow_run_id, agent, model, confidence, tokens_in,
               tokens_out, latency_ms, outcome, started_at, ended_at, output,
               retried
        FROM agent_runs
        WHERE workflow_run_id IN ({placeholders})
        ORDER BY workflow_run_id, id
        """,
        wf_ids,
    ).fetchall()
    gd_rows = conn.execute(
        f"""
        SELECT id, workflow_run_id, intent, risk, confidence, reversibility, action
        FROM governance_decisions
        WHERE workflow_run_id IN ({placeholders})
        ORDER BY workflow_run_id, id
        """,
        wf_ids,
    ).fetchall()
    rr_rows = conn.execute(
        f"""
        SELECT id, workflow_run_id, agent, failure_kind, original_error,
               strategy_attempted, peer_agent, override_model, attempt_index,
               outcome, started_at, ended_at
        FROM repair_runs
        WHERE workflow_run_id IN ({placeholders})
        ORDER BY workflow_run_id, id
        """,
        wf_ids,
    ).fetchall()

    ar_by_wf: dict[int, list[dict]] = {wf_id: [] for wf_id in wf_ids}
    for row in ar_rows:
        err = None
        try:
            out_json = _json.loads(row["output"]) if row["output"] else {}
            err = out_json.get("error")
        except Exception:
            err = None
        ar_by_wf.setdefault(row["workflow_run_id"], []).append({
            "agent": row["agent"],
            "model": row["model"],
            "confidence": row["confidence"],
            "tokens_in": row["tokens_in"],
            "tokens_out": row["tokens_out"],
            "latency_ms": row["latency_ms"],
            "outcome": row["outcome"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "error": err,
            "retried": bool(row["retried"]),
        })

    gd_by_wf: dict[int, dict] = {}
    for row in gd_rows:
        gd_by_wf[row["workflow_run_id"]] = {
            "id": row["id"],
            "action": row["action"],
            "confidence": row["confidence"],
            "intent": row["intent"],
            "risk": row["risk"],
            "reversibility": row["reversibility"],
        }

    rr_by_wf: dict[int, list[dict]] = {wf_id: [] for wf_id in wf_ids}
    for row in rr_rows:
        rr_by_wf.setdefault(row["workflow_run_id"], []).append({
            "id": row["id"],
            "agent": row["agent"],
            "failure_kind": row["failure_kind"],
            "original_error": row["original_error"],
            "strategy_attempted": row["strategy_attempted"],
            "peer_agent": row["peer_agent"],
            "override_model": row["override_model"],
            "attempt_index": row["attempt_index"],
            "outcome": row["outcome"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
        })

    out: list[dict] = []
    for row in wf_rows:
        try:
            cls = _json.loads(row["classification"]) if row["classification"] else {}
        except Exception:
            cls = {}
        try:
            wf = _json.loads(row["workflow"]) if row["workflow"] else {}
        except Exception:
            wf = {}
        out.append({
            "id": row["id"],
            "conversation_id": row["conversation_id"],
            "message_id": row["message_id"],
            "classification": cls,
            "workflow": wf,
            "execution_mode": row["execution_mode"],
            "outcome": row["outcome"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "agent_runs": ar_by_wf.get(row["id"], []),
            "governance": gd_by_wf.get(row["id"]),
            "repair_runs": rr_by_wf.get(row["id"], []),
        })
    return out


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
