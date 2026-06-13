"""Offline evaluation harness for evolution variants (Phase 17b).

This is NOT the shell sandbox (`src/ubongo/sandbox.py`). It is an isolated
harness that runs a lineage variant against the held-out conversation set and
measures five quality/cost signals, with **no real side effects**: no
`workflow_runs`, no `agent_runs`, no governance, no vault, no queue, no
durable-memory writes. The only thing the caller persists is the
`evolution_evaluations` rows. That isolation is what lets the GP loop (Phase 18)
run continuously without polluting conversation state.

For each sample conversation the harness:

1. **Generates** a response using `UBONGO.md` (global identity) + the variant
   text as the system prompt — the top two layers of the real prompt with the
   variant body substituted, and no skill / memory / agent-role layers, to
   isolate the variant's effect. The sample's prior turns are the messages.
2. **Judges** the response with one LLM call returning all three subjective
   signals at once: `{"quality", "hallucination", "would_user_correct"}`.

Per variant it aggregates across the evaluated samples into `VariantMetrics`
(fed to `evolution.fitness`). A `CallBudget` caps total LLM calls per run
(seeded from `evolution.max_calls_per_hour`); variants that cannot be fully
afforded are skipped so the cohort stays comparable.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from ubongo.agents import personas
from ubongo.config import load_config, load_evolution
from ubongo.evolution import targets
from ubongo.evolution.fitness import VariantMetrics
from ubongo.evaluation import CallBudget, parse_judgment
from ubongo.llm import LLMError, complete

logger = logging.getLogger("ubongo.evolution.sandbox")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_UBONGO_MD = _REPO_ROOT / "config" / "UBONGO.md"
_DEFAULT_SAMPLES_PATH = _REPO_ROOT / "tests" / "manual" / "fixtures" / "sample_conversations.json"

_GEN_MAX_TOKENS = 600
# 250 truncated verbose judges mid-JSON in live runs (~1/3 parse failures);
# 400 gives the flat 3-field object ample room.
_JUDGE_MAX_TOKENS = 400
_DEFAULT_SAMPLES_PER_EVAL = 5

_JUDGE_RUBRIC = (
    "You are an evaluation judge scoring an assistant response to a user. "
    "Return ONLY a JSON object with this exact shape, no prose before or after:\n\n"
    '{"quality": <float 0.0..1.0>, "hallucination": <float 0.0..1.0>, '
    '"would_user_correct": <true|false>}\n\n'
    "- quality: how well the response answers the user (1.0 = direct, complete, "
    "correct; 0.0 = useless or off-topic).\n"
    "- hallucination: how much the response asserts facts it cannot support or "
    "that are wrong (0.0 = none; 1.0 = fabricated specifics, e.g. invented "
    "numbers, names, or accepting a false premise in the question).\n"
    "- would_user_correct: true if a reasonable user would have to push back or "
    "correct the response before it is usable.\n"
    "A response that correctly declines an unknowable question or corrects a "
    "false premise has LOW hallucination and HIGH quality."
)


@dataclass(frozen=True)
class _SampleScore:
    quality: float
    hallucination: float
    would_correct: bool
    gen_tokens: int
    gen_latency_ms: int


# --- sample loading / selection ---------------------------------------------


def load_samples(path: Path | None = None) -> dict:
    """Load the held-out sample set JSON. Returns the parsed object with
    `version` and `conversations`."""
    p = path or _DEFAULT_SAMPLES_PATH
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def select_samples(sample_set: dict, target: str, limit: int) -> list[dict]:
    """Pick the samples that exercise `target`, truncated to `limit`.

    Samples whose `persona_affinity` matches the target persona plus the
    general (`null`) ones; if that yields nothing, fall back to the full set.
    Selection is deterministic (stable fixture order, no random sampling).
    """
    persona = target.split(":", 1)[1] if ":" in target else target
    conversations = sample_set.get("conversations", [])
    selected = [
        c for c in conversations
        if c.get("persona_affinity") in (persona, None)
    ]
    if not selected:
        selected = list(conversations)
    return selected[: max(0, limit)]


# --- prompt assembly + LLM calls --------------------------------------------


def _read_ubongo_md() -> str:
    try:
        return _UBONGO_MD.read_text(encoding="utf-8").rstrip()
    except OSError:
        return ""


def _build_variant_system_prompt(variant_text: str) -> str:
    """UBONGO.md global identity + the variant body. No skill / memory /
    agent-role layers — the variant is evaluated in isolation."""
    base = _read_ubongo_md()
    if base:
        return base + "\n\n" + variant_text.rstrip()
    return variant_text.rstrip()


def _score_sample(
    variant_system_prompt: str,
    sample: dict,
    *,
    gen_model: str,
    judge_model: str,
    gen_max_tokens: int,
) -> _SampleScore | None:
    """Generate a response for one sample and judge it. Returns None if either
    LLM call fails or the judgment cannot be parsed (the sample is dropped)."""
    turns = [
        {"role": t["role"], "content": t["content"]}
        for t in sample.get("turns", [])
        if t.get("role") in ("user", "assistant") and t.get("content")
    ]
    if not turns or turns[-1]["role"] != "user":
        return None

    try:
        gen = complete(
            system_prompt=variant_system_prompt,
            messages=turns,
            model=gen_model,
            max_tokens=gen_max_tokens,
        )
    except LLMError as exc:
        logger.warning("eval_generate_failed", extra={"sample": sample.get("id"), "cause": str(exc.cause) if exc.cause else None})
        return None
    response_text = gen.text.strip()
    if not response_text:
        return None

    parsed = _judge(turns[-1]["content"], response_text, judge_model=judge_model)
    if parsed is None:
        return None
    quality, hallucination, would_correct = parsed
    return _SampleScore(
        quality=quality,
        hallucination=hallucination,
        would_correct=would_correct,
        gen_tokens=gen.tokens_in + gen.tokens_out,
        gen_latency_ms=gen.latency_ms,
    )


def _judge(question: str, response_text: str, *, judge_model: str) -> tuple[float, float, bool] | None:
    """Run the 3-signal judge over a (question, response). Shared by prompt and
    config evaluation. Returns (quality, hallucination, would_correct) or None."""
    judge_system = (
        _JUDGE_RUBRIC
        + "\n\n## User question\n\n" + question
        + "\n\n## Assistant response\n\n" + response_text
    )
    try:
        judgment = complete(
            system_prompt=judge_system,
            messages=[{"role": "user", "content": "Score the response."}],
            model=judge_model,
            max_tokens=_JUDGE_MAX_TOKENS,
        )
    except LLMError as exc:
        logger.warning("eval_judge_failed", extra={"cause": str(exc.cause) if exc.cause else None})
        return None
    parsed = parse_judgment(judgment.text)
    if parsed is None:
        logger.warning("eval_judge_parse_error", extra={"raw_preview": judgment.text[:200]})
        return None
    return parsed


# --- per-variant + per-target evaluation ------------------------------------


def evaluate_variant(
    variant_row: dict,
    samples: list[dict],
    *,
    gen_model: str,
    judge_model: str,
    budget: CallBudget,
    gen_max_tokens: int = _GEN_MAX_TOKENS,
) -> VariantMetrics | None:
    """Evaluate one lineage variant against `samples`, returning aggregate
    `VariantMetrics` or None if the variant could not be evaluated (budget
    cannot cover its full sample set, or every sample was dropped).

    All-or-nothing on budget so the cohort stays comparable: a variant is only
    started if `budget.can_afford(len(samples) * CALLS_PER_SAMPLE)`.
    """
    needed = len(samples) * CallBudget.CALLS_PER_SAMPLE
    if needed == 0 or not budget.can_afford(needed):
        return None

    system_prompt = _build_variant_system_prompt(variant_row["variant_text"])
    scores: list[_SampleScore] = []
    for sample in samples:
        budget.spend(CallBudget.CALLS_PER_SAMPLE)
        score = _score_sample(
            system_prompt,
            sample,
            gen_model=gen_model,
            judge_model=judge_model,
            gen_max_tokens=gen_max_tokens,
        )
        if score is not None:
            scores.append(score)

    if not scores:
        return None

    n = len(scores)
    return VariantMetrics(
        lineage_id=variant_row["id"],
        success_rate=sum(s.quality for s in scores) / n,
        hallucination_rate=sum(s.hallucination for s in scores) / n,
        user_correction_rate=sum(1 for s in scores if s.would_correct) / n,
        cost=sum(s.gen_tokens for s in scores) / n,
        latency_ms=sum(s.gen_latency_ms for s in scores) / n,
    )


_CONFIG_CALLS_PER_SAMPLE = 5  # classify + a few agents + judge (estimate, for the budget)


def _sample_turns(sample: dict) -> list[dict] | None:
    turns = [
        {"role": t["role"], "content": t["content"]}
        for t in sample.get("turns", [])
        if t.get("role") in ("user", "assistant") and t.get("content")
    ]
    if not turns or turns[-1]["role"] != "user":
        return None
    return turns


def _run_workflow_isolated(agent_names, message: str, *, persona: str) -> tuple[str, int]:
    """Run a workflow's agents on `message` with NO persistence — no agent_runs,
    no events, no Repair ladder, no governance, no vault/queue. Returns
    (composer_text, total_tokens). This is the side-effect-free execution the
    config evaluator judges."""
    from ubongo import runner
    from ubongo.agents.base import AgentInput
    from ubongo.invoke import SequentialHarvest, resolve_agents
    from ubongo.master import Context

    registry = runner.default_registry()
    ctx = Context(conversation_id=None, persona=persona, auto_mode=False, pending_skill=None)
    # The same prior-threading + composer-pick harvest the runner's sequential
    # mode uses, so this isolated executor cannot drift from it. Dispatch is the
    # only difference: bare agent.run here vs the side-effectful runner path.
    harvest = SequentialHarvest()
    for _name, agent in resolve_agents(registry, agent_names):
        inp = AgentInput(message=message, history=(), summary_text=None,
                         prior_findings=harvest.prior, metadata={})
        try:
            res = agent.run(inp, ctx)
        except Exception:  # an agent crash must not abort the eval
            continue
        harvest.observe(agent, res)
    out = harvest.outcome()
    return out.composer_text, out.total_tokens


def _score_config_sample(target: str, parsed: dict, sample: dict, *, judge_model: str) -> _SampleScore | None:
    """Produce a response under the variant config (routing or tool-chain) and
    judge it. Returns None if the turn or judgment fails."""
    from ubongo import classifier, router

    turns = _sample_turns(sample)
    if turns is None:
        return None
    message = turns[-1]["content"]
    t0 = time.monotonic()

    if target == "routing:default":
        try:
            cls = classifier.classify(message)
        except Exception:
            return None
        with router.config_override(routing=parsed):
            wf_name = router.route_workflow(cls)
            agents = router.workflow_agents(wf_name)
            persona = router.workflow_persona(wf_name)
            response, tokens = _run_workflow_isolated(agents, message, persona=persona)
    elif target.startswith("toolchain:"):
        wf_name = target[len("toolchain:"):]
        variant_agents = parsed.get("agents", [])
        with router.config_override(toolchain={wf_name: variant_agents}):
            agents = router.workflow_agents(wf_name)
            persona = router.workflow_persona(wf_name)
            response, tokens = _run_workflow_isolated(agents, message, persona=persona)
    else:
        return None

    if not response.strip():
        return None
    parsed_judge = _judge(message, response, judge_model=judge_model)
    if parsed_judge is None:
        return None
    quality, hallucination, would_correct = parsed_judge
    return _SampleScore(
        quality=quality, hallucination=hallucination, would_correct=would_correct,
        gen_tokens=tokens, gen_latency_ms=int((time.monotonic() - t0) * 1000),
    )


def evaluate_config_variant(
    variant_row: dict,
    target: str,
    samples: list[dict],
    *,
    judge_model: str,
    budget: CallBudget,
) -> VariantMetrics | None:
    """Evaluate one config variant by running the real (overridden) pipeline on
    the samples and judging the responses. All-or-nothing on the budget so the
    cohort stays comparable. None if it can't be afforded or every sample drops."""
    needed = len(samples) * _CONFIG_CALLS_PER_SAMPLE
    if needed == 0 or not budget.can_afford(needed):
        return None
    try:
        parsed = yaml.safe_load(variant_row["variant_text"])
    except yaml.YAMLError:
        return None
    if not isinstance(parsed, dict):
        return None

    scores: list[_SampleScore] = []
    for sample in samples:
        budget.spend(_CONFIG_CALLS_PER_SAMPLE)
        score = _score_config_sample(target, parsed, sample, judge_model=judge_model)
        if score is not None:
            scores.append(score)
    if not scores:
        return None
    n = len(scores)
    return VariantMetrics(
        lineage_id=variant_row["id"],
        success_rate=sum(s.quality for s in scores) / n,
        hallucination_rate=sum(s.hallucination for s in scores) / n,
        user_correction_rate=sum(1 for s in scores if s.would_correct) / n,
        cost=sum(s.gen_tokens for s in scores) / n,
        latency_ms=sum(s.gen_latency_ms for s in scores) / n,
    )


@dataclass(frozen=True)
class TargetEvaluation:
    """Result of `evaluate_target`: the per-variant metrics that were scored
    (cohort), the sample-set version, and how many variants were skipped by the
    budget."""

    cohort: list[VariantMetrics]
    sample_set_version: str
    evaluated: int
    skipped: int
    total_variants: int


def _persona_model(target: str) -> str:
    persona_name = target.split(":", 1)[1] if ":" in target else target
    try:
        return personas.get(persona_name).model
    except Exception:
        return load_config().get("models", {}).get("default", "")


def _judge_model() -> str:
    models = load_config().get("models", {})
    return models.get("evaluator") or models.get("default", "")


def evaluate_target(
    variant_rows: list[dict],
    target: str,
    *,
    sample_set: dict | None = None,
    samples_per_eval: int | None = None,
    budget: CallBudget | None = None,
) -> TargetEvaluation:
    """Evaluate a cohort of variants (a target's generation) against the
    selected samples under a shared call budget.

    Does NOT write to the DB — the caller persists `evolution_evaluations` rows
    after computing fitness, keeping the harness side-effect-free.
    """
    evo = load_evolution()
    if sample_set is None:
        sample_set = load_samples()
    if samples_per_eval is None:
        samples_per_eval = int(evo.get("samples_per_eval", _DEFAULT_SAMPLES_PER_EVAL))
    if budget is None:
        budget = CallBudget(int(evo.get("max_calls_per_hour", 30)))

    version = sample_set.get("version", "unknown")
    samples = select_samples(sample_set, target, samples_per_eval)
    gen_model = _persona_model(target)
    judge_model = _judge_model()
    kind = targets.target_kind(target)

    cohort: list[VariantMetrics] = []
    skipped = 0
    for row in variant_rows:
        if kind == targets.CONFIG:
            metrics = evaluate_config_variant(
                row, target, samples, judge_model=judge_model, budget=budget,
            )
        else:
            metrics = evaluate_variant(
                row, samples, gen_model=gen_model, judge_model=judge_model, budget=budget,
            )
        if metrics is None:
            skipped += 1
        else:
            cohort.append(metrics)

    logger.info(
        "evaluate_target",
        extra={
            "target": target,
            "evaluated": len(cohort),
            "skipped": skipped,
            "samples": len(samples),
            "calls_spent": budget.spent,
        },
    )
    return TargetEvaluation(
        cohort=cohort,
        sample_set_version=version,
        evaluated=len(cohort),
        skipped=skipped,
        total_variants=len(variant_rows),
    )
