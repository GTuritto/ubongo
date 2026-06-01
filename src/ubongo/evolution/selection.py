"""Target + survivor selection for the autonomous GP loop (Phase 18a / 18c).

`next_target` chooses which evolvable target the loop works on next, by
staleness — the one whose last completed cycle is oldest (or which has never
run), breaking ties by registry order. Over time this round-robins across the
targets (scenario 4: round-robin visible in lineage timestamps).

`survivors` returns the top-K variants of a target's generation by fitness, the
seeds for the next generation (cross-generation lineage).
"""

from __future__ import annotations

from ubongo.evolution import targets
from ubongo.memory import store


def next_target() -> str | None:
    """Return the stalest evolvable target, or None if there are none.

    A target that has never run a cycle (`last_cycle_at` is None) is maximally
    stale and sorts first; among those that have run, the oldest `ended_at`
    wins. Registry order is the deterministic tiebreak (it is the secondary
    sort key, and ungenerated targets keep their registry order because the
    primary key is constant).
    """
    evolvable = targets.evolvable_targets()
    if not evolvable:
        return None

    def staleness_key(index_target: tuple[int, str]) -> tuple[int, str, int]:
        index, target = index_target
        last = store.last_cycle_at(target)
        # never-run (None) sorts before any timestamp; then oldest timestamp;
        # then registry order (index) as the deterministic tiebreak.
        return (0 if last is None else 1, last or "", index)

    ranked = sorted(enumerate(evolvable), key=staleness_key)
    return ranked[0][1]


def survivors(target: str, generation: int, k: int) -> list[dict]:
    """Return the top-`k` evaluated variants of `target`'s `generation`, ranked
    best-first (fitness desc, lineage_id asc — already the order
    `evaluations_for_target` returns). Each dict carries `lineage_id`,
    `variant_text`, `fitness`, `strategy`. Empty if the generation is
    unevaluated."""
    if k <= 0:
        return []
    rows = store.evaluations_for_target(target, generation=generation)
    return rows[:k]
