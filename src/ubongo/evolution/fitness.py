"""Fitness scoring for evaluated variants (Phase 17c).

A variant's raw metrics are three already-normalized subjective signals
(`success_rate`, `hallucination_rate`, `user_correction_rate`, all in [0, 1])
plus two unbounded costs (`cost` = mean total tokens per sample, `latency_ms`).
The two costs are **min-max normalized across the cohort** — the set of variants
evaluated together — so a variant is cheap/fast *relative to its siblings*.

Fitness is the weighted sum, using the inverse weights for the lower-is-better
components:

    fitness = w_success            * success_rate
            + w_cost_inverse       * (1 - norm_cost)
            + w_latency_inverse    * (1 - norm_latency)
            + w_halluc_inverse     * (1 - hallucination_rate)
            + w_correction_inverse * (1 - user_correction_rate)

Weights come from `evolution.fitness_weights` in settings.yaml. The leaderboard
sorts by fitness descending, then by `lineage_id` ascending — a fully
deterministic tiebreak.
"""

from __future__ import annotations

from dataclasses import dataclass

from ubongo.config import load_evolution

# Fallback weights if settings.yaml omits the block (sums to 1.0).
_DEFAULT_WEIGHTS: dict[str, float] = {
    "success_rate": 0.40,
    "cost_inverse": 0.15,
    "latency_inverse": 0.10,
    "hallucination_inverse": 0.20,
    "user_correction_inverse": 0.15,
}


@dataclass(frozen=True)
class VariantMetrics:
    """Raw aggregate metrics for one variant, pre-fitness.

    `lineage_id` is the tiebreak key. The three rate fields are in [0, 1];
    `cost` and `latency_ms` are unbounded and normalized across the cohort.
    """

    lineage_id: int
    success_rate: float
    hallucination_rate: float
    user_correction_rate: float
    cost: float
    latency_ms: float


def load_weights() -> dict[str, float]:
    weights = load_evolution().get("fitness_weights")
    if not isinstance(weights, dict) or not weights:
        return dict(_DEFAULT_WEIGHTS)
    merged = dict(_DEFAULT_WEIGHTS)
    for key, value in weights.items():
        try:
            merged[key] = float(value)
        except (TypeError, ValueError):
            continue
    return merged


def _min_max(values: list[float]) -> tuple[float, float]:
    return (min(values), max(values)) if values else (0.0, 0.0)


def _normalize(value: float, lo: float, hi: float) -> float:
    """Min-max normalize to [0, 1]. A flat cohort (hi == lo, e.g. a single
    variant) yields 0.0 — the best position for a lower-is-better cost, since
    there is nothing cheaper to compare against. This degenerate case is
    intentional, not a divide-by-zero guard accident.
    """
    if hi <= lo:
        return 0.0
    return (value - lo) / (hi - lo)


def compute_fitness(
    metrics: VariantMetrics,
    *,
    cost_bounds: tuple[float, float],
    latency_bounds: tuple[float, float],
    weights: dict[str, float] | None = None,
) -> float:
    """Weighted-sum fitness for one variant, given the cohort's cost/latency
    bounds (from `cohort_bounds`). Higher is better."""
    w = weights or load_weights()
    norm_cost = _normalize(metrics.cost, *cost_bounds)
    norm_latency = _normalize(metrics.latency_ms, *latency_bounds)
    return (
        w["success_rate"] * metrics.success_rate
        + w["cost_inverse"] * (1.0 - norm_cost)
        + w["latency_inverse"] * (1.0 - norm_latency)
        + w["hallucination_inverse"] * (1.0 - metrics.hallucination_rate)
        + w["user_correction_inverse"] * (1.0 - metrics.user_correction_rate)
    )


def cohort_bounds(
    cohort: list[VariantMetrics],
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return ((cost_lo, cost_hi), (latency_lo, latency_hi)) over the cohort."""
    return (
        _min_max([m.cost for m in cohort]),
        _min_max([m.latency_ms for m in cohort]),
    )


def rank_cohort(
    cohort: list[VariantMetrics],
    *,
    weights: dict[str, float] | None = None,
) -> list[tuple[VariantMetrics, float]]:
    """Score every variant against the cohort and return
    (metrics, fitness) pairs sorted best-first: fitness desc, lineage_id asc.

    The lineage_id tiebreak makes the order deterministic when two variants
    score identically (spec scenario 4).
    """
    if not cohort:
        return []
    w = weights or load_weights()
    cb, lb = cohort_bounds(cohort)
    scored = [
        (m, compute_fitness(m, cost_bounds=cb, latency_bounds=lb, weights=w))
        for m in cohort
    ]
    scored.sort(key=lambda pair: (-pair[1], pair[0].lineage_id))
    return scored
