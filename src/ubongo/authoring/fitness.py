"""Quality scalar for an authored skill candidate (Phase 2b).

A candidate is scored alone, not against a cohort, so the GP loop's cross-cohort
cost/latency min-max normalization does not apply. The score is a weighted sum of
the three subjective judge signals (the same philosophy as
`evolution.fitness.compute_fitness`, lower-is-better signals inverted), gated by
the command dry-run for command skills: a skill whose command refused or errored
is heavily penalized regardless of how good its prose reads.

Deterministic and pure — the same metrics always yield the same score.
"""

from __future__ import annotations

from ubongo.authoring.sandbox import CandidateMetrics

# Weights over the three subjective signals (sum to 1.0).
_W_QUALITY = 0.60
_W_HALLUCINATION_INV = 0.25
_W_CORRECTION_INV = 0.15

# A command skill whose dry-run failed keeps only this fraction of its prose
# score — bad enough to sink it below any working skill, not a hard zero (the
# prose may still be salvageable on a re-draft).
_BROKEN_COMMAND_FACTOR = 0.4


def score_candidate(metrics: CandidateMetrics) -> float:
    """Return a single quality score in [0, 1]. Higher is better."""
    prose = (
        _W_QUALITY * metrics.quality
        + _W_HALLUCINATION_INV * (1.0 - metrics.hallucination)
        + _W_CORRECTION_INV * (1.0 - metrics.would_correct_rate)
    )
    if metrics.command_ok is None:
        return prose
    # Command skill: a clean dry-run keeps the full prose score; a failed one is
    # multiplied down so it ranks below every working candidate.
    factor = 1.0 if metrics.command_ok >= 1.0 else _BROKEN_COMMAND_FACTOR
    return prose * factor
