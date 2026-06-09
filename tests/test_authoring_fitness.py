from __future__ import annotations

import os

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.authoring.fitness import score_candidate  # noqa: E402
from ubongo.authoring.sandbox import CandidateMetrics  # noqa: E402


def _metrics(quality=0.8, halluc=0.1, corr=0.0, command_ok=None) -> CandidateMetrics:
    return CandidateMetrics(quality=quality, hallucination=halluc,
                            would_correct_rate=corr, command_ok=command_ok,
                            probes=2, tokens=10, latency_ms=10)


def test_prompt_score_formula() -> None:
    # 0.6*0.8 + 0.25*(1-0.1) + 0.15*(1-0.0) = 0.48 + 0.225 + 0.15
    assert score_candidate(_metrics()) == pytest.approx(0.855)


def test_working_command_keeps_full_score() -> None:
    assert score_candidate(_metrics(command_ok=1.0)) == pytest.approx(0.855)


def test_broken_command_penalized() -> None:
    assert score_candidate(_metrics(command_ok=0.0)) == pytest.approx(0.855 * 0.4)


def test_score_bounded_0_1() -> None:
    assert score_candidate(_metrics(quality=1.0, halluc=0.0, corr=0.0)) == pytest.approx(1.0)
    assert score_candidate(_metrics(quality=0.0, halluc=1.0, corr=1.0)) == pytest.approx(0.0)


def test_hallucination_and_correction_lower_score() -> None:
    clean = score_candidate(_metrics(halluc=0.0, corr=0.0))
    dirty = score_candidate(_metrics(halluc=0.8, corr=1.0))
    assert dirty < clean


def test_deterministic() -> None:
    m = _metrics(command_ok=1.0)
    assert score_candidate(m) == score_candidate(m)
