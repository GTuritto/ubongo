from __future__ import annotations

import os

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.evolution.fitness import (  # noqa: E402
    VariantMetrics,
    cohort_bounds,
    compute_fitness,
    load_weights,
    rank_cohort,
)

_W = {
    "success_rate": 0.40,
    "cost_inverse": 0.15,
    "latency_inverse": 0.10,
    "hallucination_inverse": 0.20,
    "user_correction_inverse": 0.15,
}


def _m(lineage_id, success=0.8, halluc=0.1, corr=0.0, cost=100.0, latency=50.0):
    return VariantMetrics(lineage_id, success, halluc, corr, cost, latency)


def test_weighted_sum_all_perfect_is_one() -> None:
    m = _m(1, success=1.0, halluc=0.0, corr=0.0, cost=100, latency=50)
    # Single-variant cohort: norm_cost = norm_latency = 0 (best).
    fit = compute_fitness(m, cost_bounds=(100, 100), latency_bounds=(50, 50), weights=_W)
    assert abs(fit - 1.0) < 1e-9


def test_cheaper_variant_wins_when_quality_equal() -> None:
    cheap = _m(1, cost=100, latency=50)
    pricey = _m(2, cost=200, latency=80)
    ranked = rank_cohort([cheap, pricey], weights=_W)
    assert [m.lineage_id for m, _ in ranked] == [1, 2]
    assert ranked[0][1] > ranked[1][1]


def test_cohort_normalization_is_relative() -> None:
    # Cost only matters relative to siblings; absolute magnitude is normalized.
    a = _m(1, cost=1000, latency=50)
    b = _m(2, cost=2000, latency=50)
    cb, lb = cohort_bounds([a, b])
    assert cb == (1000, 2000)
    assert lb == (50, 50)
    fa = compute_fitness(a, cost_bounds=cb, latency_bounds=lb, weights=_W)
    fb = compute_fitness(b, cost_bounds=cb, latency_bounds=lb, weights=_W)
    assert fa > fb  # a is the cheaper of the two


def test_single_variant_degenerate_no_divide_by_zero() -> None:
    m = _m(1, cost=999, latency=999)
    ranked = rank_cohort([m], weights=_W)
    assert len(ranked) == 1
    # norm cost/latency collapse to 0 (best), so only the rate terms vary.
    fit = ranked[0][1]
    assert 0.0 <= fit <= 1.0


def test_deterministic_tiebreak_lower_lineage_first() -> None:
    # Identical metrics -> identical fitness -> lower lineage_id ranks first.
    a = _m(7)
    b = _m(3)
    c = _m(5)
    ranked = rank_cohort([a, b, c], weights=_W)
    assert [m.lineage_id for m, _ in ranked] == [3, 5, 7]
    fits = [f for _, f in ranked]
    assert fits[0] == fits[1] == fits[2]


def test_hallucination_degrades_fitness() -> None:
    clean = _m(1, halluc=0.0)
    dirty = _m(2, halluc=0.9)
    ranked = rank_cohort([clean, dirty], weights=_W)
    assert ranked[0][0].lineage_id == 1
    assert ranked[0][1] > ranked[1][1]


def test_load_weights_from_config_sums_to_one() -> None:
    w = load_weights()
    assert abs(sum(w.values()) - 1.0) < 1e-6
    assert set(w) >= set(_W)


def test_empty_cohort() -> None:
    assert rank_cohort([], weights=_W) == []
