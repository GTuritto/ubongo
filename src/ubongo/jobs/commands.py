"""The standing-jobs command pack (v0.5 phase 06).

`/jobs [status|list|pause|resume|off|run <name>]` — read + control only (job
definitions live in config/jobs.yaml; D1: no live CRUD of scheduled side
effects). Handler contract per ubongo.commands (pure: line + ReplState -> text).
"""

from __future__ import annotations

import logging

from ubongo.commands import Command, ReplState

logger = logging.getLogger("ubongo.jobs.commands")

_SUBCOMMANDS = ("status", "list", "pause", "resume", "off", "run")
_USAGE = "Usage: /jobs [status|list|pause|resume|off|run <name>]."


def _parse(line: str) -> tuple[str, str]:
    """`/jobs run news-digest` -> ("run", "news-digest"); `/jobs` -> ("status", "")."""
    parts = line.strip().lstrip("/").split(maxsplit=1)
    if not parts or parts[0].lower() != "jobs":
        return ("status", "")
    rest = parts[1].strip() if len(parts) > 1 else ""
    if not rest:
        return ("status", "")
    bits = rest.split(maxsplit=1)
    return (bits[0].lower(), bits[1].strip() if len(bits) > 1 else "")


def _render_status() -> str:
    from ubongo.config import load_job_definitions, load_jobs
    from ubongo.memory import jobs_state

    cfg = load_jobs()
    enabled = bool(cfg.get("enabled", False))
    status = jobs_state.get_jobs_status()
    cap = int(cfg.get("max_runs_per_hour", 20))
    used = jobs_state.runs_in_last_hour()
    cron = cfg.get("cron")
    pace = "continuous" if cron is None else f"every {cron}s"
    quiet = cfg.get("quiet_hours")
    quiet_str = f"{quiet[0]:02d}:00–{quiet[1]:02d}:00" if quiet and len(quiet) == 2 else "none"

    lines = [
        f"Standing jobs: status={status}  enabled={enabled}  "
        f"throttle={used}/{cap} runs in last hour  pacing={pace}  quiet={quiet_str}",
    ]
    if not enabled:
        lines.append("  (jobs.enabled is false in settings.yaml — the loop thread does not start.)")
    defs = [d for d in load_job_definitions() if isinstance(d, dict) and d.get("name")]
    if not defs:
        lines.append("  no jobs defined in config/jobs.yaml.")
    for d in defs:
        row = jobs_state.get_job(d["name"]) or {}
        on = "enabled" if d.get("enabled") else "disabled"
        last = row.get("last_run") or "never"
        nxt = row.get("next_run") or "due"
        outcome = row.get("last_outcome") or "—"
        lines.append(f"  {d['name']:<16} {on:<8} last={last} next={nxt} ({outcome})")
    return "\n".join(lines)


def _render_runs() -> str:
    from ubongo.memory import jobs_state

    runs = jobs_state.job_runs_recent(10)
    if not runs:
        return "No job runs recorded yet."
    lines = ["Recent job runs (last 10):"]
    for r in runs:
        did = f" decision#{r['decision_id']}" if r.get("decision_id") else ""
        lines.append(f"  #{r['id']} {r['job_name']:<16} {r['outcome']}{did}  {r.get('detail') or ''}")
    return "\n".join(lines)


def _render_control(sub: str) -> str:
    from ubongo.config import load_jobs
    from ubongo.memory import jobs_state

    if sub == "resume":
        jobs_state.set_jobs_status("running")
        if not load_jobs().get("enabled", False):
            return ("Status set to running, but jobs.enabled is false in settings.yaml "
                    "so the loop thread is not active. Enable it and restart the REPL.")
        return "Standing jobs resumed (status=running). Due jobs will run, throttled."
    if sub == "pause":
        jobs_state.set_jobs_status("paused")
        return "Standing jobs paused. The in-flight cycle finishes; no new ones start."
    if sub == "off":
        jobs_state.set_jobs_status("off")
        return "Standing jobs off. They idle until /jobs resume."
    return _USAGE


def _render_run(name: str) -> str:
    from ubongo.config import load_job_definitions
    from ubongo.jobs import runner

    if not name:
        return f"Usage: /jobs run <name>. {_render_status()}"
    match = next((d for d in load_job_definitions()
                  if isinstance(d, dict) and d.get("name") == name), None)
    if match is None:
        return f"Unknown job: {name}."
    result = runner.run_job(match)
    if result.outcome == "parked":
        return (f"Job '{name}' parked — needs approval (decision #{result.decision_id}). "
                f"Approve with /approve {result.decision_id}.")
    if result.outcome == "error":
        return f"Job '{name}' errored: {result.note}"
    return f"Job '{name}' {result.outcome}."


def render(line: str) -> str:
    """Parse + dispatch a `/jobs ...` line to its renderer. State-free, so both
    the REPL command and the `ubongo jobs` CLI share one path."""
    sub, arg = _parse(line)
    if sub == "status":
        return _render_status()
    if sub == "list":
        return _render_runs()
    if sub in ("pause", "resume", "off"):
        return _render_control(sub)
    if sub == "run":
        return _render_run(arg)
    return f"Unknown subcommand: {sub}. {_USAGE}"


def _cmd_jobs(line: str, state: ReplState) -> str | None:
    return render(line)


COMMANDS: dict[str, Command] = {
    "jobs": Command(_cmd_jobs, "/jobs [status|list|pause|resume|off|run <name>]"),
}
