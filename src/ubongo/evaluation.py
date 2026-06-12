"""Evaluation-common helpers shared by the two side-effect-free sandboxes.

evolution/sandbox.py (cohort fitness over held-out samples) and
authoring/sandbox.py (single-candidate quality over generated probes) stay
separate evaluators; the LLM-judge plumbing they share lives here once: the
call budget, the tolerant judgment-JSON parsing (judges sometimes wrap the
JSON in prose or a code fence despite the rubric), and the unified-diff
preview both approval surfaces render. Distinct from agents/evaluator.py,
whose judgment shape (confidence/issues) belongs to the Evaluator worker."""

from __future__ import annotations

import json
import re

_CODE_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)
# Fallback: pluck the first flat {...} object out of prose-wrapped output. The
# judgment JSON has no nested objects, so a brace-free body match is safe.
_JSON_OBJECT_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


class CallBudget:
    """Caps the number of LLM calls in one evaluation run.

    Seeded from the owning loop's `max_calls_per_hour`. `can_afford(n)` checks
    whether `n` more calls fit; `spend(n)` consumes them. A variant/candidate is
    evaluated all-or-nothing (its full sample set) so cohorts stay comparable —
    callers check `can_afford(n_samples * CALLS_PER_SAMPLE)` before starting.

    Note (Phase 17 scope): this is a per-run cap, not a true cross-run
    rolling-hour window. Rate-over-time across processes is the daemons'
    concern (`daemon.should_cycle`).
    """

    CALLS_PER_SAMPLE = 2  # one generate + one judge

    def __init__(self, limit: int) -> None:
        self.limit = max(0, int(limit))
        self.spent = 0

    def remaining(self) -> int:
        return max(0, self.limit - self.spent)

    def can_afford(self, n: int) -> bool:
        return self.spent + n <= self.limit

    def spend(self, n: int) -> None:
        self.spent += n


def strip_code_fence(text: str) -> str:
    match = _CODE_FENCE_RE.match(text)
    return match.group(1) if match else text.strip()


def load_judgment_object(raw: str) -> dict | None:
    """Get the judgment dict from the raw judge output. Tries, in order:
    the fence-stripped whole string, then the first flat {...} object embedded
    in prose (judges sometimes wrap the JSON in explanation despite the rubric).
    """
    cleaned = strip_code_fence(raw)
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    match = _JSON_OBJECT_RE.search(cleaned)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def parse_judgment(raw: str) -> tuple[float, float, bool] | None:
    """Parse the sandbox-judge JSON. Returns (quality, hallucination,
    would_correct) or None on failure."""
    data = load_judgment_object(raw)
    if data is None:
        return None
    try:
        quality = max(0.0, min(1.0, float(data["quality"])))
        hallucination = max(0.0, min(1.0, float(data["hallucination"])))
    except (KeyError, TypeError, ValueError):
        return None
    would_correct = bool(data.get("would_user_correct", False))
    return quality, hallucination, would_correct


def diff_preview(base: str, variant: str, *, context: int = 2) -> list[str]:
    """A compact unified diff of base→variant (prompts, skill bodies, or
    serialized config) for the /improvements and /skill-candidates lists."""
    import difflib

    diff = difflib.unified_diff(
        base.splitlines(), variant.splitlines(),
        fromfile="active", tofile="candidate", lineterm="", n=context,
    )
    lines = list(diff)
    if len(lines) > 24:
        lines = lines[:24] + [f"    … ({len(lines) - 24} more diff lines)"]
    return lines
