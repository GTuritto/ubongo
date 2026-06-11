"""The local profiler: aggregate run stats + opt-in CPU profiling (candidate 10).

Two halves, both stdlib-only and read-only against durable memory:

**Stats** — on-demand aggregation over the `workflow_runs` / `agent_runs` rows
the runner already persists every turn. Pure functions fetch with read-only
SELECTs via :func:`store.connection` and fold in Python (p95 needs the raw
latency list anyway; single-user scale). Nothing here writes — the Memory
Agent's single-writer rule is untouched.

**CPU** — :func:`profile_call` wraps one call (the turn's `master.handle`) in
``cProfile``, dumps ``data/profiles/turn-<ts>.prof`` (loadable in snakeviz /
pstats) and returns a top-25 cumulative summary. Armed explicitly via
`/profile cpu on` or `ubongo send --profile`; zero overhead when off. A
profiling failure never breaks the turn: the wrapped call's result always
comes back, the report degrades to None. Known accepted limitation: cProfile
sees the whole process, including event-loop idle time.
"""

from __future__ import annotations

import cProfile
import io
import logging
import pstats
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ubongo.memory import store

logger = logging.getLogger("ubongo.profiling")


# ---------- stats: aggregation ----------


@dataclass(frozen=True)
class SummaryStats:
    """The `/profile` headline: turn-level latency + token totals."""

    turns: int
    avg_latency_ms: float | None  # None when no run has both timestamps yet
    p95_latency_ms: float | None
    tokens_in: int
    tokens_out: int
    slowest_agent: str | None  # by total latency across its runs
    slowest_agent_ms: int


@dataclass(frozen=True)
class GroupStats:
    """One row of a `/profile agents|models|modes` breakdown."""

    key: str
    runs: int
    avg_latency_ms: float | None
    p95_latency_ms: float | None
    tokens_in: int
    tokens_out: int
    failures: int
    retried: int

    @property
    def failure_pct(self) -> float:
        return 100.0 * self.failures / self.runs if self.runs else 0.0


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _p95(values: list[float]) -> float | None:
    """Nearest-rank p95 over the raw list; None when empty."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-95 * len(ordered) // 100))  # ceil(0.95 * n)
    return ordered[rank - 1]


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _recent_workflow_ids(last_n: int | None) -> list[int] | None:
    """Ids of the most recent N workflow runs, or None for "all" (no filter)."""
    if last_n is None:
        return None
    rows = store.connection().execute(
        "SELECT id FROM workflow_runs ORDER BY id DESC LIMIT ?", (last_n,)
    ).fetchall()
    return [int(r["id"]) for r in rows]


def _workflow_rows(last_n: int | None) -> list:
    sql = "SELECT id, execution_mode, started_at, ended_at, outcome FROM workflow_runs"
    params: tuple = ()
    if last_n is not None:
        sql += " ORDER BY id DESC LIMIT ?"
        params = (last_n,)
    return store.connection().execute(sql, params).fetchall()


def _agent_rows(last_n: int | None) -> list:
    sql = (
        "SELECT agent, model, latency_ms, tokens_in, tokens_out, outcome, retried"
        " FROM agent_runs"
    )
    params: tuple = ()
    ids = _recent_workflow_ids(last_n)
    if ids is not None:
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        sql += f" WHERE workflow_run_id IN ({placeholders})"
        params = tuple(ids)
    return store.connection().execute(sql, params).fetchall()


def _workflow_latency_ms(row) -> float | None:
    """Wall latency from started_at/ended_at; workflow_runs has no latency column."""
    if not row["started_at"] or not row["ended_at"]:
        return None
    try:
        delta = _parse_iso(row["ended_at"]) - _parse_iso(row["started_at"])
    except ValueError:
        return None
    return delta.total_seconds() * 1000.0


def summary(last_n: int | None = None) -> SummaryStats | None:
    """Headline stats over the last N workflow runs (all when N is None).

    Returns None when no workflow has run yet."""
    wf_rows = _workflow_rows(last_n)
    if not wf_rows:
        return None
    latencies = [ms for r in wf_rows if (ms := _workflow_latency_ms(r)) is not None]
    agent_rows = _agent_rows(last_n)
    per_agent_ms: dict[str, int] = {}
    tokens_in = tokens_out = 0
    for r in agent_rows:
        tokens_in += r["tokens_in"] or 0
        tokens_out += r["tokens_out"] or 0
        per_agent_ms[r["agent"]] = per_agent_ms.get(r["agent"], 0) + (r["latency_ms"] or 0)
    slowest = max(per_agent_ms.items(), key=lambda kv: kv[1]) if per_agent_ms else None
    return SummaryStats(
        turns=len(wf_rows),
        avg_latency_ms=_avg(latencies),
        p95_latency_ms=_p95(latencies),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        slowest_agent=slowest[0] if slowest else None,
        slowest_agent_ms=slowest[1] if slowest else 0,
    )


def _group_agent_rows(rows, key_of) -> list[GroupStats]:
    """Fold agent_runs rows into GroupStats keyed by `key_of(row)`, sorted by
    total latency descending so the most expensive group reads first."""
    buckets: dict[str, list] = {}
    for r in rows:
        buckets.setdefault(key_of(r), []).append(r)
    groups = []
    for key, members in buckets.items():
        latencies = [float(r["latency_ms"]) for r in members if r["latency_ms"] is not None]
        groups.append(GroupStats(
            key=key,
            runs=len(members),
            avg_latency_ms=_avg(latencies),
            p95_latency_ms=_p95(latencies),
            tokens_in=sum(r["tokens_in"] or 0 for r in members),
            tokens_out=sum(r["tokens_out"] or 0 for r in members),
            failures=sum(1 for r in members if r["outcome"] != "success"),
            retried=sum(1 for r in members if r["retried"]),
        ))
    # most expensive first: total latency = avg * runs
    return sorted(groups, key=lambda g: (g.avg_latency_ms or 0.0) * g.runs, reverse=True)


def by_agent(last_n: int | None = None) -> list[GroupStats]:
    return _group_agent_rows(_agent_rows(last_n), lambda r: r["agent"])


def by_model(last_n: int | None = None) -> list[GroupStats]:
    return _group_agent_rows(_agent_rows(last_n), lambda r: r["model"] or "—")


def by_mode(last_n: int | None = None) -> list[GroupStats]:
    """Per execution_mode, over workflow-level wall latency (not agent rows)."""
    buckets: dict[str, list] = {}
    for r in _workflow_rows(last_n):
        buckets.setdefault(r["execution_mode"], []).append(r)
    groups = []
    for mode, members in buckets.items():
        latencies = [ms for r in members if (ms := _workflow_latency_ms(r)) is not None]
        groups.append(GroupStats(
            key=mode,
            runs=len(members),
            avg_latency_ms=_avg(latencies),
            p95_latency_ms=_p95(latencies),
            tokens_in=0,  # token columns live on agent_runs, not workflow_runs
            tokens_out=0,
            failures=sum(1 for r in members if r["outcome"] == "failure"),
            retried=sum(1 for r in members if r["outcome"] == "repaired"),
        ))
    return sorted(groups, key=lambda g: g.runs, reverse=True)


# ---------- stats: rendering ----------

_NO_RUNS = "No runs recorded yet."


def _fmt_ms(ms: float | None) -> str:
    return "—" if ms is None else f"{ms:.0f}"


def _window_label(last_n: int | None) -> str:
    return "all runs" if last_n is None else f"last {last_n} runs"


def render_summary(last_n: int | None = None) -> str:
    s = summary(last_n)
    if s is None:
        return _NO_RUNS
    lines = [
        f"Profile ({_window_label(last_n)}):",
        f"  turns: {s.turns}  avg {_fmt_ms(s.avg_latency_ms)} ms  p95 {_fmt_ms(s.p95_latency_ms)} ms",
        f"  tokens: {s.tokens_in} in / {s.tokens_out} out",
    ]
    if s.slowest_agent is not None:
        lines.append(f"  slowest agent: {s.slowest_agent} ({s.slowest_agent_ms} ms total)")
    return "\n".join(lines)


def _render_groups(title: str, groups: list[GroupStats], last_n: int | None,
                   *, with_tokens: bool = True) -> str:
    if not groups:
        return _NO_RUNS
    header = f"  {'key':<30}  {'runs':>4}  {'avg ms':>7}  {'p95 ms':>7}"
    if with_tokens:
        header += f"  {'tok in':>8}  {'tok out':>8}"
    header += f"  {'fail%':>5}  {'retried':>7}"
    lines = [f"{title} ({_window_label(last_n)}):", header]
    for g in groups:
        row = (
            f"  {g.key[:30]:<30}  {g.runs:>4}  {_fmt_ms(g.avg_latency_ms):>7}"
            f"  {_fmt_ms(g.p95_latency_ms):>7}"
        )
        if with_tokens:
            row += f"  {g.tokens_in:>8}  {g.tokens_out:>8}"
        row += f"  {g.failure_pct:>5.1f}  {g.retried:>7}"
        lines.append(row)
    return "\n".join(lines)


def render_agents(last_n: int | None = None) -> str:
    return _render_groups("Per-agent profile", by_agent(last_n), last_n)


def render_models(last_n: int | None = None) -> str:
    return _render_groups("Per-model profile", by_model(last_n), last_n)


def render_modes(last_n: int | None = None) -> str:
    # retried column counts 'repaired' workflows here; tokens are agent-level
    # so the mode table omits them.
    return _render_groups("Per-mode profile", by_mode(last_n), last_n, with_tokens=False)


# ---------- CPU profiling ----------

PROFILE_TOP_N = 25


def profiles_dir() -> Path:
    """Sibling of the SQLite db (data/profiles/), so test set_db_path redirects it."""
    return store.get_db_path().parent / "profiles"


def _report_path() -> Path:
    directory = profiles_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = directory / f"turn-{stamp}.prof"
    counter = 2
    while path.exists():  # same-second collisions
        path = directory / f"turn-{stamp}-{counter}.prof"
        counter += 1
    return path


def _write_report(prof: cProfile.Profile) -> str:
    path = _report_path()
    prof.dump_stats(str(path))
    buf = io.StringIO()
    stats = pstats.Stats(prof, stream=buf)
    stats.strip_dirs().sort_stats("cumulative").print_stats(PROFILE_TOP_N)
    return f"CPU profile written to {path}\nTop {PROFILE_TOP_N} by cumulative time:\n{buf.getvalue()}"


def profile_call(fn, /, *args, **kwargs) -> tuple[object, str | None]:
    """Run `fn(*args, **kwargs)` under cProfile.

    Returns (result, report_text). Profiling is best-effort: if the profiler
    cannot start or the report cannot be written, the call still runs/returns
    and the report is None. An exception from `fn` itself propagates unchanged
    (the profiler is disabled first; no report is written for a failed turn).
    """
    prof = cProfile.Profile()
    try:
        prof.enable()
    except Exception:
        # e.g. another profiler is already active on this thread
        logger.warning("cpu_profile_unavailable", exc_info=True)
        return fn(*args, **kwargs), None
    try:
        result = fn(*args, **kwargs)
    finally:
        prof.disable()
    try:
        report = _write_report(prof)
    except Exception:
        logger.warning("cpu_profile_report_failed", exc_info=True)
        report = None
    return result, report
