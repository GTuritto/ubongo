"""Promotion proposer + approve/reject/rollback orchestration (Phase 19d/f/g).

The autonomous loop proposes a promotion when a cycle's champion beats the
active baseline by `evolution.promotion_margin`; the user decides via
`/improvements`. Approval performs the live swap (writes `active_evolutions` and
busts the runtime caches) so the promoted variant actually changes behavior, and
appends a row to the audit log. Nothing promotes autonomously.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ubongo import events
from ubongo.config import load_evolution
from ubongo.memory import store, vault

logger = logging.getLogger("ubongo.evolution.promotion")

_DEFAULT_MARGIN = 0.05


@dataclass(frozen=True)
class Decision:
    target: str
    lineage_id: int
    action: str  # "approve" | "reject" | "rollback"
    baseline_fitness: float | None = None
    champion_fitness: float | None = None


def baseline_fitness(target: str, generation: int) -> float:
    """The fitness to beat: the active promotion's latest evaluation when one
    exists, else the best fitness among prior generations (the incumbent), else
    0.0 (nothing promoted, no history)."""
    active = store.active_evolution(target)
    if active is not None:
        ev = store.latest_evaluation_for_lineage(active["lineage_id"])
        return float(ev["fitness"]) if ev else 0.0
    prior = [r for r in store.evaluations_for_target(target) if r["generation"] < generation]
    return max((float(r["fitness"]) for r in prior), default=0.0)


def propose_if_better(target: str, generation: int) -> int | None:
    """If `generation`'s champion beats the baseline by the margin and the
    target has no open promotion, enqueue a pending promotion. Returns the
    promotion id or None. Called by the loop after a cohort is ranked."""
    if store.has_open_promotion(target):
        return None
    evals = store.evaluations_for_target(target, generation=generation)
    if not evals:
        return None
    champion = evals[0]  # ranked fitness desc, lineage asc
    base = baseline_fitness(target, generation)
    margin = float(load_evolution().get("promotion_margin", _DEFAULT_MARGIN))
    if champion["fitness"] < base + margin:
        return None
    pid = store.append_pending_promotion(target=target, lineage_id=champion["lineage_id"])
    events.dispatch("evolution_promotion", {
        "event": "proposed", "promotion_id": pid, "target": target,
        "lineage_id": champion["lineage_id"], "baseline": base,
        "champion": champion["fitness"],
    })
    logger.info("promotion_proposed", extra={
        "promotion_id": pid, "target": target, "baseline": base,
        "champion": champion["fitness"],
    })
    return pid


def _bust_caches() -> None:
    """Make a live swap take effect immediately in the running process."""
    from ubongo import context, router
    from ubongo.agents import personas

    context.reload()
    personas.reload()
    router.reload()


def _audit(action: str, target: str, lineage_id: int, *, delta: str = "") -> None:
    line = f"**{action}** {target} (lineage #{lineage_id}){(' — ' + delta) if delta else ''}"
    try:
        vault.append_audit(store.now_iso(), line)
    except OSError as exc:  # audit is best-effort; never block a decision
        logger.warning("evolution_audit_failed", extra={"error": str(exc)})


def approve(promotion_id: int) -> Decision | None:
    """Approve a pending promotion: record the decision, set the active
    evolution (live swap), bust caches, and audit. Returns the Decision or None
    if the promotion id is unknown / already decided."""
    pending = store.get_pending_promotion(promotion_id)
    if pending is None or pending["decided_at"] is not None:
        return None
    target, lineage_id = pending["target"], pending["lineage_id"]
    ev = store.latest_evaluation_for_lineage(lineage_id)
    champ = float(ev["fitness"]) if ev else None
    base = baseline_fitness(target, pending_generation(lineage_id))

    store.decide_promotion(promotion_id, "approved")
    store.set_active_evolution(target, lineage_id)
    _bust_caches()
    delta = f"fitness {base:.3f} → {champ:.3f}" if champ is not None else ""
    _audit("approve", target, lineage_id, delta=delta)
    events.dispatch("evolution_promotion", {
        "event": "approved", "promotion_id": promotion_id, "target": target,
        "lineage_id": lineage_id,
    })
    logger.info("promotion_approved", extra={"promotion_id": promotion_id, "target": target})
    return Decision(target, lineage_id, "approve", base, champ)


def reject(promotion_id: int) -> Decision | None:
    """Reject a pending promotion: record + audit. No active change."""
    pending = store.get_pending_promotion(promotion_id)
    if pending is None or pending["decided_at"] is not None:
        return None
    store.decide_promotion(promotion_id, "rejected")
    _audit("reject", pending["target"], pending["lineage_id"])
    logger.info("promotion_rejected", extra={"promotion_id": promotion_id})
    return Decision(pending["target"], pending["lineage_id"], "reject")


def rollback(target: str) -> bool:
    """Revert a target to its file/default: clear the active evolution, bust
    caches, audit. Returns True if a promotion was active."""
    active = store.active_evolution(target)
    if active is None:
        return False
    removed = store.clear_active_evolution(target)
    if removed:
        _bust_caches()
        _audit("rollback", target, active["lineage_id"])
        logger.info("promotion_rolledback", extra={"target": target})
    return removed


def pending_generation(lineage_id: int) -> int:
    """The generation of a lineage row (for baseline computation on approve)."""
    row = store.lineage_row(lineage_id)
    return row["generation"] if row else 0
