"""Side-effect-free evaluation of an authored skill candidate (Phase 2a).

This is NOT the shell sandbox (`ubongo.sandbox`) nor the GP harness
(`ubongo.evolution.sandbox`) — it is the authoring equivalent: it scores ONE
drafted candidate so the approval gate has a quality number, and it writes
nothing durable (no skills registered, no DB rows; the caller persists the
`quality` it returns). It reuses `evolution.sandbox.CallBudget` so the budget
discipline is identical.

For a candidate it:

1. Builds a system prompt from `UBONGO.md` + the candidate's SKILL.md body (the
   layers a real turn assembles when the skill activates), in isolation.
2. Runs that over a few short probe messages and judges each response with a
   3-signal judge (quality / hallucination / would-correct), tailored with the
   skill's stated purpose so the judge scores fit.
3. For a command skill, additionally DRY-RUNS the command template through the
   real `ubongo.sandbox.run_constrained` (allowlisted, already safe) and records
   whether it ran cleanly — a command skill whose command refuses or errors is a
   bad skill regardless of its prose.

`complete` is module-level so tests patch `ubongo.authoring.sandbox.complete`.
`UBONGO_DISABLE_AUTHORING_EVAL=1` makes evaluation a no-op (returns None) so the
suite stays offline by default.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from ubongo import sandbox as shell_sandbox
from ubongo.authoring.candidate import SkillCandidate
from ubongo.config import load_authoring, load_config
from ubongo.evaluation import CallBudget, parse_judgment
from ubongo.llm import LLMError, complete

logger = logging.getLogger("ubongo.authoring.sandbox")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_UBONGO_MD = _REPO_ROOT / "config" / "UBONGO.md"
_PROBES_PATH = _REPO_ROOT / "tests" / "manual" / "fixtures" / "authoring_probes.json"

_GEN_MAX_TOKENS = 500
_JUDGE_MAX_TOKENS = 400
_DEFAULT_SAMPLES_PER_EVAL = 3

# Fallback generic probe openers if the fixture is missing. The first probe is
# always the candidate's own stated purpose (its canonical use case); these fill
# out the rest deterministically.
_GENERIC_PROBES: tuple[str, ...] = (
    "Walk me through how you would handle this.",
    "Give me the result.",
    "What do you need from me to do this?",
)


def _generic_probes() -> list[str]:
    """Load the generic probe openers from the fixture, falling back to the
    inline defaults (mirrors how evolution.sandbox loads its sample set)."""
    try:
        data = json.loads(_PROBES_PATH.read_text(encoding="utf-8"))
        probes = [str(p) for p in data.get("probes", []) if str(p).strip()]
        return probes or list(_GENERIC_PROBES)
    except (OSError, json.JSONDecodeError, ValueError):
        return list(_GENERIC_PROBES)

@dataclass(frozen=True)
class CandidateMetrics:
    """Aggregate evaluation signals for one candidate (all rates in [0, 1]).

    `command_ok` is None for a pure-prompt skill; for a command skill it is 1.0
    if the dry-run ran cleanly (not refused, exit 0) else 0.0. `probes` is how
    many judge scores were collected; `note` carries a short status."""

    quality: float
    hallucination: float
    would_correct_rate: float
    command_ok: float | None
    probes: int
    tokens: int
    latency_ms: int
    note: str = ""


def _eval_disabled() -> bool:
    return os.environ.get("UBONGO_DISABLE_AUTHORING_EVAL") == "1"


def _read_ubongo_md() -> str:
    try:
        return _UBONGO_MD.read_text(encoding="utf-8").rstrip()
    except OSError:
        return ""


def _skill_system_prompt(candidate: SkillCandidate) -> str:
    """UBONGO.md global identity + the candidate's skill body, mirroring the
    `## Active Skill` layer a real turn appends — no persona/memory layers, so
    the skill is judged in isolation."""
    base = _read_ubongo_md()
    body = f"## Active Skill: {candidate.name}\n\n{candidate.body.rstrip()}"
    if candidate.is_command_skill:
        body += f"\n\nThis skill runs the command: `{candidate.command_template.strip()}`"
    return (base + "\n\n" + body) if base else body


def _probes(candidate: SkillCandidate, n: int) -> list[str]:
    probes = [candidate.description.strip()]
    probes.extend(_generic_probes())
    return probes[: max(1, n)]


def _judge_rubric(purpose: str) -> str:
    return (
        "You are scoring an assistant response produced under a NEW skill whose "
        f"stated purpose is:\n  {purpose}\n\n"
        "Return ONLY a JSON object, no prose:\n"
        '{"quality": <0.0..1.0>, "hallucination": <0.0..1.0>, '
        '"would_user_correct": <true|false>}\n\n'
        "- quality: how well the response serves that purpose for the user.\n"
        "- hallucination: how much it asserts unsupported or invented specifics.\n"
        "- would_user_correct: true if a reasonable user would have to push back "
        "before the response is usable.\n"
        "A response that correctly declines an unknowable request or asks one "
        "sharp clarifying question has LOW hallucination and acceptable quality."
    )


def _gen_model() -> str:
    configured = (load_authoring() or {}).get("model")
    models = load_config().get("models", {})
    return configured or models.get("coding") or models.get("default", "")


def _judge_model() -> str:
    models = load_config().get("models", {})
    return models.get("evaluator") or models.get("default", "")


def _dry_run_command(candidate: SkillCandidate) -> float | None:
    """Run a command skill's template through the real shell sandbox once. Returns
    1.0 on a clean run, 0.0 on refusal/error, None for a non-command skill."""
    if not candidate.is_command_skill:
        return None
    try:
        result = shell_sandbox.run_constrained(candidate.command_template)
    except shell_sandbox.SandboxRefused:
        return 0.0
    return 1.0 if result.exit_code == 0 else 0.0


def evaluate_candidate(
    candidate: SkillCandidate,
    *,
    samples_per_eval: int | None = None,
    budget: CallBudget | None = None,
    gen_model: str | None = None,
    judge_model: str | None = None,
) -> CandidateMetrics | None:
    """Score one candidate, side-effect-free. Returns None when evaluation is
    disabled, the budget cannot cover the probe set, or every probe was dropped
    (so the caller leaves `quality` unset rather than recording a fake 0)."""
    if _eval_disabled():
        return None

    n = samples_per_eval if samples_per_eval is not None else int(
        (load_authoring() or {}).get("samples_per_eval", _DEFAULT_SAMPLES_PER_EVAL)
    )
    probes = _probes(candidate, n)
    if budget is None:
        budget = CallBudget(len(probes) * CallBudget.CALLS_PER_SAMPLE)
    needed = len(probes) * CallBudget.CALLS_PER_SAMPLE
    if needed == 0 or not budget.can_afford(needed):
        return None

    command_ok = _dry_run_command(candidate)  # local, no LLM call
    system_prompt = _skill_system_prompt(candidate)
    rubric = _judge_rubric(candidate.description.strip())
    gm = gen_model or _gen_model()
    jm = judge_model or _judge_model()

    qualities: list[float] = []
    halluc: list[float] = []
    corrections = 0
    tokens = 0
    latency = 0
    for probe in probes:
        budget.spend(CallBudget.CALLS_PER_SAMPLE)
        try:
            gen = complete(system_prompt=system_prompt,
                           messages=[{"role": "user", "content": probe}],
                           model=gm, max_tokens=_GEN_MAX_TOKENS)
        except LLMError as exc:
            logger.warning("authoring_eval_gen_failed",
                           extra={"cause": str(exc.cause) if exc.cause else None})
            continue
        response = gen.text.strip()
        if not response:
            continue
        try:
            judgment = complete(
                system_prompt=rubric + "\n\n## User message\n\n" + probe
                + "\n\n## Assistant response\n\n" + response,
                messages=[{"role": "user", "content": "Score the response."}],
                model=jm, max_tokens=_JUDGE_MAX_TOKENS)
        except LLMError as exc:
            logger.warning("authoring_eval_judge_failed",
                           extra={"cause": str(exc.cause) if exc.cause else None})
            continue
        parsed = parse_judgment(judgment.text)
        if parsed is None:
            continue
        q, h, wc = parsed
        qualities.append(q)
        halluc.append(h)
        corrections += 1 if wc else 0
        tokens += gen.tokens_in + gen.tokens_out
        latency += gen.latency_ms

    if not qualities:
        # No usable judge score. For a command skill we still know whether the
        # command ran, but with no quality signal we decline to fabricate one.
        return None

    scored = len(qualities)
    metrics = CandidateMetrics(
        quality=sum(qualities) / scored,
        hallucination=sum(halluc) / scored,
        would_correct_rate=corrections / scored,
        command_ok=command_ok,
        probes=scored,
        tokens=tokens // scored,
        latency_ms=latency // scored,
        note=("command skill" if candidate.is_command_skill else "prompt skill"),
    )
    logger.info("authoring_evaluated",
                extra={"skill_name": candidate.name, "quality": metrics.quality,
                       "command_ok": command_ok, "probes": scored})
    return metrics
