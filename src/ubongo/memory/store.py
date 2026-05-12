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
        _connection = sqlite3.connect(_DB_PATH, isolation_level=None)
        _connection.row_factory = sqlite3.Row
        _connection.execute("PRAGMA foreign_keys = ON")
    if not _bootstrapped:
        schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        _connection.executescript(schema_sql)
        _bootstrapped = True
    return _connection


def connection() -> sqlite3.Connection:
    return bootstrap()


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
               g.workflow_run_id, w.execution_mode, w.workflow
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
            "action": row["action"],
            "workflow_run_id": row["workflow_run_id"],
            "execution_mode": row["execution_mode"],
            "persona": persona,
        })
    return out
