"""Variant persistence to `evolution_lineage` (Phase 16c).

`record_variants(target, variants)` computes the next generation, resolves the
parent pointer, and writes one lineage row per variant via `store`. Raw SQL
stays in `memory/store.py`; this module owns the domain decisions (generation
numbering, parent resolution, metadata shape).
"""

from __future__ import annotations

from ubongo.evolution.generator import Variant
from ubongo.memory import evolution_state
from ubongo.memory import store


def next_generation(target: str) -> int:
    """The generation number a fresh `record_variants` call will use.

    One past the highest recorded generation for the target — so the first run
    writes generation 1 (spec scenario 1), the next writes 2, and so on.
    """
    return evolution_state.max_lineage_generation(target) + 1


def record_variants(target: str, variants: list[Variant]) -> list[int]:
    """Persist `variants` as one new generation for `target`; return row ids.

    All variants in a call share one generation and one parent: the currently
    promoted active variant (`evolution_state.active_lineage_id`) when one exists, else
    NULL — always NULL in Phase 16, since no promotions exist yet (scenario 3).
    """
    if not variants:
        return []

    generation = next_generation(target)
    # Phase 18: a variant mutated from a prior survivor carries its own
    # parent_id (cross-generation lineage). Otherwise fall back to the
    # currently-promoted active variant (Phase 16 behavior — NULL until a
    # Phase 19 promotion exists).
    active_parent = evolution_state.active_lineage_id(target)

    ids: list[int] = []
    for variant in variants:
        metadata = {"strategy": variant.strategy, **variant.metadata}
        parent_id = variant.parent_id if variant.parent_id is not None else active_parent
        row_id = evolution_state.append_lineage_variant(
            target=target,
            parent_id=parent_id,
            generation=generation,
            variant_text=variant.text,
            variant_metadata=metadata,
        )
        ids.append(row_id)
    return ids
