"""Manual (user-driven) evolution entry points behind /optimize and /evaluate.

These own the GP business logic the REPL used to carry inline: generate +
persist a generation of variants, and score a target's latest generation +
persist the cohort evaluations. The command handlers in ubongo.commands call
these and format the result; the REPL no longer reaches into generator /
lineage / sandbox / loop directly. The autonomous counterpart is evolution/loop.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from ubongo.config import load_evolution
from ubongo.evolution import generator, lineage, loop, sandbox
from ubongo.evolution.targets import UnknownTargetError, is_target
from ubongo.memory import evolution_state
from ubongo.memory import store


class NoVariantsError(Exception):
    """Raised by score_latest_generation when a target has no generated variants."""

    def __init__(self, target: str) -> None:
        super().__init__(target)
        self.target = target


@dataclass(frozen=True)
class GenerateOutcome:
    target: str
    generation: int
    requested: int
    variants: list  # list[generator.Variant]
    ids: list[int]


def generate_variants(target: str, n: int | None = None) -> GenerateOutcome:
    """Generate and persist one generation of variants for `target`.

    No master.handle, no governance, no enqueue — a direct tool. Writes
    evolution_lineage rows. Raises UnknownTargetError for an unknown target.
    """
    if n is None:
        try:
            n = int(load_evolution().get("population_size", 8))
        except (TypeError, ValueError):
            n = 8
    variants = generator.generate(target, n)  # raises UnknownTargetError
    if not variants:
        return GenerateOutcome(target=target, generation=0, requested=n, variants=[], ids=[])
    ids = lineage.record_variants(target, variants)
    generation = lineage.next_generation(target) - 1  # record_variants just used this
    return GenerateOutcome(
        target=target, generation=generation, requested=n, variants=variants, ids=ids
    )


@dataclass(frozen=True)
class CohortOutcome:
    target: str
    generation: int
    result: object  # sandbox.TargetEvaluation
    ranked: list  # list[(VariantMetrics, fitness)] — empty when cohort empty
    strategy_by_id: dict[int, str | None]


def score_latest_generation(target: str) -> CohortOutcome:
    """Score the target's latest generation, persisting evolution_evaluations.

    The sandbox harness is side-effect-free; only evaluation rows are written,
    after fitness is computed across the cohort. Raises UnknownTargetError for an
    unknown target and NoVariantsError when no generation exists yet.
    """
    if not is_target(target):
        raise UnknownTargetError(target)
    generation = evolution_state.max_lineage_generation(target)
    if generation == 0:
        raise NoVariantsError(target)

    variant_rows = evolution_state.lineage_for_target(target, generation=generation)
    strategy_by_id = {
        r["id"]: (r["variant_metadata"] or {}).get("strategy") for r in variant_rows
    }
    result = sandbox.evaluate_target(variant_rows, target)  # raises UnknownTargetError
    ranked = loop.persist_cohort_evaluations(result) if result.cohort else []
    return CohortOutcome(
        target=target, generation=generation, result=result,
        ranked=ranked, strategy_by_id=strategy_by_id,
    )
