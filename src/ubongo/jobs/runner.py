"""Standing-jobs runner (v0.5 phase 06): one cycle, one job.

`run_one_cycle` is pure and synchronous (no sleeps) — the daemon scheduler calls
it off-thread, and `/jobs run` / the tests drive it directly. A cycle:

1. sweeps expired parked raises (default-deny: auto-decline, the job retries);
2. picks the single most-overdue enabled job and runs its turn.

A job's turn goes through `master.handle` (no bypass: classified, governed,
persisted). On `require_approval` the job *parks* — master has already written
the `pending_approvals` record — and raises itself proactively for approve-later.
Otherwise the composed text is enqueued as a proactive row, held behind quiet
hours when the window is closed. Completion is one log line.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from ubongo import master
from ubongo.config import load_job_definitions, load_jobs
from ubongo.delivery import queue
from ubongo.jobs import delivery, policy
from ubongo.memory import jobs_state
from ubongo.memory.store import _now, now_iso

logger = logging.getLogger("ubongo.jobs.runner")

_DEFAULT_TTL_HOURS = 24.0


@dataclass(frozen=True)
class JobResult:
    job: str | None
    outcome: str  # delivered | held | parked | skipped | error | idle
    decision_id: int | None = None
    note: str = ""


def enabled_jobs() -> list[dict]:
    """Config-defined jobs with enabled=true, each runtime row ensured."""
    out = []
    for d in load_job_definitions():
        if not isinstance(d, dict) or not d.get("name"):
            continue
        if not d.get("enabled", False):
            continue
        jobs_state.ensure_job(d["name"])
        out.append(d)
    return out


def due_job(now: str | None = None) -> dict | None:
    """The single most-overdue enabled job (lowest next_run that is past), or
    None when nothing is due. A job with no next_run is due immediately."""
    when = now or now_iso()
    best: tuple[str, dict] | None = None  # (next_run sort key, def)
    for d in enabled_jobs():
        row = jobs_state.get_job(d["name"]) or {}
        nxt = row.get("next_run")
        if nxt is not None and nxt > when:
            continue  # scheduled in the future
        key = nxt or ""  # NULL (never run) sorts first
        if best is None or key < best[0]:
            best = (key, d)
    return best[1] if best else None


def _sweep_expired_raises() -> list[JobResult]:
    """Auto-decline parked raises past the TTL (default-deny, AC-7). Each becomes
    a 'skipped' job_run; the job retries on its next schedule."""
    ttl_hours = float(load_jobs().get("raise_ttl_hours", _DEFAULT_TTL_HOURS))
    results: list[JobResult] = []
    for row in jobs_state.expired_parked_decisions(ttl_hours * 3600.0):
        decision_id = row["decision_id"]
        try:
            master.resume_approval(decision_id, "n")  # idempotent decline
        except Exception:
            logger.warning("job_raise_autodecline_failed",
                           extra={"decision_id": decision_id}, exc_info=True)
            continue
        jobs_state.record_job_run(row["job_name"], outcome="skipped",
                                  decision_id=decision_id, detail="raise expired (default-deny)")
        logger.info("job_raise_expired",
                    extra={"job": row["job_name"], "decision_id": decision_id})
        results.append(JobResult(job=row["job_name"], outcome="skipped", decision_id=decision_id))
    return results


def run_job(job: dict) -> JobResult:
    """Run one job's turn and apply the proactive-delivery policy."""
    name = job["name"]
    persona = job.get("persona", "operator")
    prompt = (job.get("prompt") or "").strip()
    workflow = job.get("workflow")  # pending_workflow, e.g. connector_session
    schedule_s = int(job.get("schedule_seconds", 86400))
    jobs_cfg = load_jobs()
    quiet = jobs_cfg.get("quiet_hours")
    ttl_hours = float(jobs_cfg.get("raise_ttl_hours", _DEFAULT_TTL_HOURS))

    run_id = jobs_state.start_job_run(name)
    next_run = policy._iso(_now() + timedelta(seconds=schedule_s))

    try:
        response = master.handle(prompt, persona, auto_mode=True, pending_workflow=workflow)
    except Exception as exc:
        jobs_state.finish_job_run(run_id, outcome="error", detail=str(exc))
        jobs_state.mark_run(name, last_run=now_iso(), next_run=next_run, last_outcome="error")
        logger.warning("job_turn_failed", extra={"job": name, "cause": str(exc)})
        return JobResult(job=name, outcome="error", note=str(exc))

    # Flush the turn's own queue row so the turn is recorded + vault-projected
    # like any turn. The user-facing push is the SEPARATE proactive row below.
    queue.flush_delivered(response.delivery_token)

    if response.approval is not None:
        decision_id = response.approval.decision_id
        raise_text = (
            f"Job '{name}' needs approval: {response.approval.summary} "
            f"Reply /approve {decision_id} or /decline {decision_id}."
        )
        queue.enqueue(
            raise_text, urgency="urgent", source=delivery.SOURCE_RAISE,
            expires_at=policy.raise_expires_at(ttl_hours),
            metadata={"job": name, "decision_id": decision_id},
        )
        jobs_state.finish_job_run(run_id, outcome="parked", decision_id=decision_id,
                                  detail="raised for approval")
        jobs_state.mark_run(name, last_run=now_iso(), next_run=next_run, last_outcome="parked")
        logger.info("job_parked", extra={"job": name, "decision_id": decision_id})
        return JobResult(job=name, outcome="parked", decision_id=decision_id)

    deliver_after = policy.deliver_after(quiet)
    queue.enqueue(
        response.text, urgency="normal", source=delivery.SOURCE_PROACTIVE,
        deliver_after=deliver_after, metadata={"job": name},
    )
    outcome = "held" if deliver_after else "delivered"
    jobs_state.finish_job_run(run_id, outcome=outcome,
                              detail=("held for quiet hours" if deliver_after else "delivered"))
    jobs_state.mark_run(name, last_run=now_iso(), next_run=next_run, last_outcome=outcome)
    logger.info("job_completed", extra={"job": name, "outcome": outcome})
    return JobResult(job=name, outcome=outcome)


def run_one_cycle() -> JobResult:
    """Sweep expired raises, then run the most-overdue due job (one per cycle)."""
    _sweep_expired_raises()
    job = due_job()
    if job is None:
        return JobResult(job=None, outcome="idle", note="no due jobs")
    return run_job(job)
