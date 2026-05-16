"""Confidence scoring (Phase 14c).

Two confidence signals exist: the classifier's self-reported `confidence` in
its classification, and the Evaluator's LLM-as-judge score for the answer
(`WorkflowResult.evaluator_confidence`). For the governance reject rule the
evaluator's score is authoritative — it judges the actual answer. When no
evaluator ran (e.g. a casual workflow) the classifier's confidence is the
only signal available, so it is the fallback.

This formalizes the `stored_confidence` selection master already does inline.
"""

from __future__ import annotations


def score_confidence(classification, workflow_result) -> float:
    """Return the governing confidence for this turn.

    Evaluator confidence when present; classifier confidence otherwise.
    """
    evaluator = getattr(workflow_result, "evaluator_confidence", None)
    if evaluator is not None:
        return float(evaluator)
    classifier = getattr(classification, "confidence", 0.0)
    try:
        return float(classifier)
    except (TypeError, ValueError):
        return 0.0


def has_evaluator_signal(workflow_result) -> bool:
    """True when an Evaluator produced a score this turn.

    The reject rule only fires on the evaluator's judgment of the answer —
    never on the classifier's confidence in its own classification.
    """
    return getattr(workflow_result, "evaluator_confidence", None) is not None
