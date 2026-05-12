from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

from ubongo.memory import store

logger = logging.getLogger("ubongo.delivery.queue")

Urgency = Literal["low", "normal", "urgent"]

_URGENCY_RANK = {"urgent": 0, "normal": 1, "low": 2}


@dataclass(frozen=True)
class QueueRow:
    id: int
    content: str
    urgency: Urgency
    source: str | None
    created_at: str
    deliver_after: str | None
    delivered_at: str | None
    expires_at: str | None
    metadata: dict[str, Any] | None


def _row_to_queue(row) -> QueueRow:
    raw_meta = row["metadata"]
    metadata: dict[str, Any] | None = None
    if raw_meta:
        try:
            metadata = json.loads(raw_meta)
        except json.JSONDecodeError:
            logger.warning("queue_metadata_decode_failed", extra={"row_id": row["id"]})
            metadata = None
    return QueueRow(
        id=row["id"],
        content=row["content"],
        urgency=row["urgency"],
        source=row["source"],
        created_at=row["created_at"],
        deliver_after=row["deliver_after"],
        delivered_at=row["delivered_at"],
        expires_at=row["expires_at"],
        metadata=metadata,
    )


def enqueue(
    content: str,
    *,
    urgency: Urgency = "urgent",
    source: str | None = None,
    deliver_after: str | None = None,
    expires_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    if urgency not in _URGENCY_RANK:
        raise ValueError(f"invalid urgency {urgency!r}; expected low|normal|urgent")
    conn = store.connection()
    cursor = conn.execute(
        """
        INSERT INTO notification_queue
            (content, urgency, source, created_at, deliver_after, expires_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            content,
            urgency,
            source,
            store.now_iso(),
            deliver_after,
            expires_at,
            json.dumps(metadata) if metadata is not None else None,
        ),
    )
    return int(cursor.lastrowid)


def dequeue_deliverable(now: str | None = None) -> QueueRow | None:
    """Return the next deliverable row without marking it delivered.

    Filters out rows that are already delivered, scheduled in the future, or
    expired. Ordered by urgency (urgent first) then created_at ascending.
    """
    when = now or store.now_iso()
    conn = store.connection()
    row = conn.execute(
        """
        SELECT id, content, urgency, source, created_at,
               deliver_after, delivered_at, expires_at, metadata
        FROM notification_queue
        WHERE delivered_at IS NULL
          AND (deliver_after IS NULL OR deliver_after <= ?)
          AND (expires_at IS NULL OR expires_at > ?)
        ORDER BY
            CASE urgency WHEN 'urgent' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
            created_at ASC
        LIMIT 1
        """,
        (when, when),
    ).fetchone()
    if row is None:
        return None
    return _row_to_queue(row)


def mark_delivered(row_id: int, when: str | None = None) -> None:
    ts = when or store.now_iso()
    conn = store.connection()
    conn.execute(
        "UPDATE notification_queue SET delivered_at = ? WHERE id = ?",
        (ts, row_id),
    )


def last_n(n: int = 10) -> list[QueueRow]:
    if n <= 0:
        return []
    conn = store.connection()
    rows = conn.execute(
        """
        SELECT id, content, urgency, source, created_at,
               deliver_after, delivered_at, expires_at, metadata
        FROM notification_queue
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (n,),
    ).fetchall()
    return [_row_to_queue(r) for r in rows]
