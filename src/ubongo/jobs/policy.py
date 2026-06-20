"""Proactive delivery policy (v0.5 phase 06): quiet hours + raise TTL.

Pure time math over the store clock (`store._now()`, which honors
`UBONGO_FAKE_NOW` so tests never wait real time). The policy decides *when* a
proactive message may reach the user:

- **Quiet hours** — a `[start, end]` 24h window (wrap-around supported). A send
  inside the window is *held*: it is queued with a `deliver_after` set to the
  next open boundary, so the queue's existing `dequeue_deliverable` filter
  surfaces it as a catch-up once the window opens. No new delivery mechanism.
- **Raise TTL** — a parked approval raise gets an `expires_at` so an unanswered
  raise stops being deliverable; the loop's sweep then auto-declines it
  (default-deny) and the job retries on its next schedule.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ubongo.memory.store import _now


def _iso(dt: datetime) -> str:
    """Format a datetime the way the store does (millisecond UTC, trailing Z)."""
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _window(quiet_hours) -> tuple[int, int] | None:
    """Normalize the [start, end] config to an (int, int) pair, or None when
    quiet hours are unset / malformed (never quiet)."""
    if not quiet_hours or len(quiet_hours) != 2:
        return None
    try:
        start, end = int(quiet_hours[0]), int(quiet_hours[1])
    except (TypeError, ValueError):
        return None
    if start == end:
        return None
    if not (0 <= start <= 23 and 0 <= end <= 23):
        return None
    return start, end


def in_quiet_hours(now: datetime, quiet_hours) -> bool:
    win = _window(quiet_hours)
    if win is None:
        return False
    start, end = win
    h = now.hour
    if start < end:
        return start <= h < end
    return h >= start or h < end  # wrap-around (e.g. 23 -> 7)


def deliver_after(quiet_hours, *, now: datetime | None = None) -> str | None:
    """The `deliver_after` for a proactive send: the next open-window boundary
    when currently inside quiet hours, else None (deliverable now)."""
    now = now or _now()
    win = _window(quiet_hours)
    if win is None or not in_quiet_hours(now, quiet_hours):
        return None
    _, end = win
    boundary = now.replace(hour=end, minute=0, second=0, microsecond=0)
    if boundary <= now:
        boundary = boundary + timedelta(days=1)
    return _iso(boundary)


def raise_expires_at(ttl_hours: float, *, now: datetime | None = None) -> str:
    """The `expires_at` for an approval raise: now + TTL."""
    now = now or _now()
    return _iso(now + timedelta(hours=float(ttl_hours)))
