from __future__ import annotations

import logging
import sys

from ubongo import channel, master, memory, profiling  # noqa: F401  -- registers after_llm seam
from ubongo.delivery import queue
from ubongo.repl import DEFAULT_PERSONA, VALID_PERSONAS

logger = logging.getLogger("ubongo.oneshot")


def run(message: str, persona: str | None = None,
        profile: str | bool | None = None) -> int:
    chosen = persona or DEFAULT_PERSONA
    if chosen not in VALID_PERSONAS:
        valid = ", ".join(VALID_PERSONAS)
        print(
            f"Error: unknown persona '{chosen}'. Choose from: {valid}.",
            file=sys.stderr,
        )
        return 1

    # Candidates 10 + 12 + 14: `ubongo send --profile[=cpu|mem|all]` (or the
    # UBONGO_PROFILE env knob, resolved by __main__). True normalizes to "cpu"
    # so candidate-10 callers are unchanged. The turn envelope (cProfile wrap,
    # master.handle, queue flush) lives in the channel core; one-shot keeps
    # only presentation: printing, the mem-report flow, and exit codes.
    if profile is True:
        profile = "cpu"
    if profile in ("mem", "all"):
        profiling.mem_start()
    response, cpu_report = channel.run_turn(
        message, chosen, profile_cpu=profile in ("cpu", "all")
    )
    print(response.text)
    reports = [r for r in (cpu_report,) if r]
    if profile in ("mem", "all"):
        mem_report = profiling.mem_report()
        if mem_report:
            reports.append(mem_report)
        profiling.mem_stop()
    for report in reports:
        # Mirror repl.emit: command-style output still goes through the
        # notification queue (ADR-0002), tagged source="command".
        token = queue.enqueue_for_delivery(
            report, source="command", after_send_payload=None
        )
        print(report)
        queue.flush_delivered(token)
    # Phase 15 / v0.5 phase 03: one-shot is non-interactive — a gated turn
    # cannot be approved here. It exits non-zero, BUT the pending record now
    # persists, so `ubongo pending` / `ubongo approve <id>` (below) or any other
    # channel can resolve it later.
    if response.approval is not None:
        print(
            f"(held for approval — approve later with: "
            f"ubongo approve {response.approval.decision_id})",
            file=sys.stderr,
        )
        return 1
    return 0 if response.ok else 1


def list_pending() -> int:
    """`ubongo pending` — the CLI surface for require_approval turns raised
    anywhere (a prior gated one-shot, an MCP call, another session)."""
    from ubongo.governance import approval as gov_approval

    rows = gov_approval.list_pending()
    if not rows:
        print("No pending approvals.")
        return 0
    print(f"Pending approvals ({len(rows)}):")
    for p in rows:
        snippet = p.message.strip().replace("\n", " ")
        if len(snippet) > 60:
            snippet = snippet[:57] + "..."
        print(f"  #{p.decision_id}  {p.created_at}  {p.persona:<10}  {snippet}")
    print("Approve with: ubongo approve <id>   (or: ubongo decline <id>)")
    return 0


def grants(revoke_id: int | None = None) -> int:
    """`ubongo grants` lists active grants; `ubongo grants revoke <id>` revokes
    one. The grant registry is DB-backed, so this works across sessions."""
    from ubongo.memory import grant_state

    if revoke_id is not None:
        if grant_state.revoke(revoke_id):
            print(f"Revoked grant #{revoke_id}. That capability will ask again next time.")
            return 0
        print(f"No active grant #{revoke_id} (unknown or already revoked).", file=sys.stderr)
        return 1
    rows = grant_state.active_grants()
    if not rows:
        print("No active grants. Connector capabilities ask on first use.")
        return 0
    print(f"Active grants ({len(rows)}):")
    for g in rows:
        print(f"  #{g['id']}  {g['created_at']}  {g['capability_class']:<24}  "
              f"{g['consequence_class']:<12}  scope={g['scope']}")
    print("Revoke with: ubongo grants revoke <id>")
    return 0


def jobs(action: str = "status", name: str | None = None) -> int:
    """`ubongo jobs [status|list|pause|resume|off|run <name>]` — the CLI surface
    for the standing-jobs daemon (v0.5 phase 06). Shares the REPL renderers."""
    from ubongo.jobs import commands as jobs_commands

    line = f"jobs {action}" + (f" {name}" if name else "")
    print(jobs_commands.render(line))
    return 0


def resolve_pending(decision_id: int, approve: bool) -> int:
    """`ubongo approve|decline <id>` — resolve a held turn through the shared
    seam. On approve the delivered answer is printed; the record is the source
    of truth, so the original channel need not still be running."""
    from ubongo.governance import approval as gov_approval

    pending = gov_approval.get_pending(decision_id)
    if pending is None or pending.status != "pending":
        print(
            f"No pending approval #{decision_id} (unknown or already resolved).",
            file=sys.stderr,
        )
        return 1
    if not approve:
        master.resume_approval(decision_id, "n")
        print(f"Declined #{decision_id}; nothing was done.")
        return 0
    resumed = master.resume_approval(decision_id, "y")
    print(resumed.text)
    return 0 if resumed.ok else 1
