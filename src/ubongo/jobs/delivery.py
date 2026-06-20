"""Proactive delivery (v0.5 phase 06): the drain side of standing jobs.

A standing job's user-facing output is enqueued as a proactive row (distinct
`source` from a typed turn's `response` row, which is the turn's own artifact).
A *drain* — run by whatever channel is listening (the REPL as a launch catch-up,
the Telegram bot each poll) — pops the deliverable proactive rows and pushes
them, marking each delivered. This keeps ADR-0002 intact: proactive output still
flows through `notification_queue`; the drain is just the reader.

Deliverability is the queue's existing filter: not delivered, `deliver_after`
passed (quiet-hours hold), not expired (raise TTL).
"""

from __future__ import annotations

from typing import Callable

from ubongo.delivery import queue
from ubongo.memory import store

SOURCE_PROACTIVE = "proactive"        # a job's composed output
SOURCE_RAISE = "proactive-raise"      # a parked job's approval ask
PROACTIVE_SOURCES = (SOURCE_PROACTIVE, SOURCE_RAISE)


def deliverable_proactive(now: str | None = None) -> list[queue.QueueRow]:
    """Undelivered proactive rows whose deliver_after has passed and which have
    not expired, oldest first."""
    when = now or store.now_iso()
    placeholders = ",".join("?" for _ in PROACTIVE_SOURCES)
    rows = store.connection().execute(
        f"""
        SELECT id, content, urgency, source, created_at,
               deliver_after, delivered_at, expires_at, metadata
        FROM notification_queue
        WHERE delivered_at IS NULL
          AND source IN ({placeholders})
          AND (deliver_after IS NULL OR deliver_after <= ?)
          AND (expires_at IS NULL OR expires_at > ?)
        ORDER BY created_at ASC, id ASC
        """,
        (*PROACTIVE_SOURCES, when, when),
    ).fetchall()
    return [queue._row_to_queue(r) for r in rows]


def drain_proactive(send: Callable[[queue.QueueRow], None], *, now: str | None = None) -> int:
    """Push every deliverable proactive row via `send` and mark it delivered.
    Returns the count sent. A `send` that raises leaves that row pending (the
    next drain retries it) and stops the run, so a dead channel never loses
    messages."""
    sent = 0
    for row in deliverable_proactive(now=now):
        send(row)  # may raise; row stays pending, surfaced again next drain
        queue.mark_delivered(row.id)
        sent += 1
    return sent
