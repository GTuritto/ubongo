"""Mutation strategies + variant generation (Phase 16a).

`generate(target, n)` produces `n` strategy-diverse prompt variants for an
evolvable target. Five strategies, four LLM-driven and one pure metadata:

    paraphrase           rewrite preserving meaning, change surface form
    prune                drop least load-bearing parts, tighten
    expand               add specificity without changing the role
    recombine            blend the base with a peer target's prompt
    perturb_temperature  same text, a sampling-temperature delta (no LLM call)

Variants are allocated by round-robin over the strategy list, so a population
of 8 is never all-paraphrase (spec scenario 2). `recombine` is skipped when the
target has no peer and the round-robin advances, so the count is still met. An
LLM strategy that raises `LLMError` is logged and dropped; the run continues
(a short run is acceptable, a crash is not).

Phase 16 has no autonomous loop, so no rate guard is wired here — but `generate`
is the single entry point a Phase 18 throttle can wrap.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ubongo.config import load_config
from ubongo.evolution import targets
from ubongo.llm import LLMError, complete

logger = logging.getLogger("ubongo.evolution.generator")

# Round-robin order. perturb_temperature is last so an 8-population run is
# weighted toward the LLM strategies (paraphrase/prune/expand twice each).
STRATEGY_ORDER: tuple[str, ...] = (
    "paraphrase",
    "prune",
    "expand",
    "recombine",
    "perturb_temperature",
)

_DEFAULT_GENERATOR_MODEL = "openrouter/anthropic/claude-sonnet-4.5"
_MAX_TOKENS = 1024

# Fixed, deterministic temperature deltas for successive perturb variants —
# Math.random is neither available nor reproducible. Cycled by occurrence.
_TEMPERATURE_DELTAS: tuple[float, ...] = (0.2, -0.2, 0.4, -0.4)

_SYSTEM_PROMPTS: dict[str, str] = {
    "paraphrase": (
        "You rewrite system prompts. Produce a single alternative that preserves "
        "the exact meaning, role, and every constraint of the prompt below, but "
        "changes the surface wording and sentence structure. Do not add or remove "
        "instructions. Output only the rewritten prompt, no preamble."
    ),
    "prune": (
        "You tighten system prompts. Produce a shorter, denser version of the "
        "prompt below: remove the least load-bearing sentences and redundant "
        "phrasing while keeping the role and all essential constraints intact. "
        "Output only the pruned prompt, no preamble."
    ),
    "expand": (
        "You sharpen system prompts. Produce a more specific version of the prompt "
        "below: add concrete instructions and edge-case handling that make the "
        "behaviour more precise, without changing the role or contradicting any "
        "existing instruction. Output only the expanded prompt, no preamble."
    ),
    "recombine": (
        "You blend two system prompts. The PRIMARY prompt defines the role and must "
        "stay dominant. Fold in the most useful instincts of the SECONDARY prompt "
        "where they strengthen the primary, without diluting its role. Output only "
        "the single blended prompt, no preamble."
    ),
}


@dataclass(frozen=True)
class Variant:
    """One generated variant, pre-persistence.

    `text` is the variant prompt; `strategy` names the mutation; `metadata`
    carries provenance (base source, peer target, temperature delta) and is
    stored as `variant_metadata`.
    """

    strategy: str
    text: str
    metadata: dict = field(default_factory=dict)
    # Phase 18: when this variant was mutated from a prior generation's
    # survivor, the survivor's lineage id (cross-generation lineage). None for
    # gen-1 variants seeded from the base prompt.
    parent_id: int | None = None


def _generator_model() -> str:
    models = load_config().get("models", {})
    return models.get("evolution_generator") or models.get("default") or _DEFAULT_GENERATOR_MODEL


def _llm_variant(
    strategy: str,
    system_prompt: str,
    user_content: str,
    metadata: dict,
    *,
    parent_id: int | None = None,
    budget=None,
) -> Variant | None:
    """Run one generator LLM call; return a Variant or None on failure.

    Phase 18: when `budget` is given, the call is gated — if the budget can't
    afford it the strategy is skipped (returns None, no call); otherwise one
    call is spent before invoking the model so the hourly cap is never exceeded.
    """
    if budget is not None and not budget.can_afford(1):
        return None
    if budget is not None:
        budget.spend(1)
    try:
        result = complete(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_content}],
            model=_generator_model(),
            max_tokens=_MAX_TOKENS,
        )
    except LLMError as exc:
        logger.warning(
            "evolution_strategy_failed",
            extra={"strategy": strategy, "cause": str(exc.cause) if exc.cause else None},
        )
        return None
    text = result.text.strip()
    if not text:
        logger.warning("evolution_strategy_empty", extra={"strategy": strategy})
        return None
    return Variant(strategy=strategy, text=text, metadata=metadata, parent_id=parent_id)


def _make_variant(
    strategy: str,
    target: str,
    base: str,
    occurrence: int,
    *,
    base_source: str,
    parent_id: int | None = None,
    budget=None,
) -> Variant | None:
    """Materialize one variant for the given strategy, or None if it can't run.

    `occurrence` is how many of this strategy have already been produced this
    run — used to vary the temperature delta and keep successive perturb /
    paraphrase variants distinct.

    Phase 18: `base` may be a survivor's text (cross-generation), `base_source`
    records its provenance, `parent_id` links the variant to that survivor, and
    `budget` gates the LLM strategies.
    """
    if strategy == "perturb_temperature":
        delta = _TEMPERATURE_DELTAS[occurrence % len(_TEMPERATURE_DELTAS)]
        # Pure metadata mutation: the prompt is unchanged, only sampling temp.
        # Free — never gated by the budget.
        return Variant(
            strategy=strategy,
            text=base,
            metadata={"base_source": base_source, "temperature_delta": delta},
            parent_id=parent_id,
        )

    if strategy == "recombine":
        peer = targets.peer_of(target)
        if peer is None:
            return None
        peer_text = targets.resolve_base(peer)
        user_content = (
            f"PRIMARY prompt:\n{base}\n\nSECONDARY prompt:\n{peer_text}"
        )
        return _llm_variant(
            strategy,
            _SYSTEM_PROMPTS[strategy],
            user_content,
            {"base_source": base_source, "peer": peer},
            parent_id=parent_id,
            budget=budget,
        )

    # paraphrase / prune / expand
    return _llm_variant(
        strategy,
        _SYSTEM_PROMPTS[strategy],
        base,
        {"base_source": base_source, "occurrence": occurrence},
        parent_id=parent_id,
        budget=budget,
    )


def generate(
    target: str,
    n: int,
    *,
    budget=None,
    parent_text: str | None = None,
    parent_id: int | None = None,
) -> list[Variant]:
    """Return up to `n` strategy-diverse variants for `target`.

    Round-robins over STRATEGY_ORDER. A strategy that cannot run (recombine
    without a peer) or fails (LLMError, empty output) is skipped and the
    round-robin advances, so diversity holds and the run never crashes. The
    result may be shorter than `n` if strategies fail; callers surface the
    actual count.

    Phase 18: when `parent_text` is given the variants mutate from it (a prior
    generation's survivor) instead of the base prompt, and carry `parent_id`
    (cross-generation lineage). When `budget` (a sandbox.CallBudget) is given,
    LLM strategies are gated so the run cannot exceed it; perturb_temperature is
    free and still fills the population. Phase 16 callers pass none of these and
    are unaffected.
    """
    if n <= 0:
        return []
    if parent_text is not None:
        base = parent_text
        base_source = f"parent:{parent_id}" if parent_id is not None else "parent"
    else:
        base = targets.resolve_base(target)  # raises UnknownTargetError for bad targets
        base_source = f"base:{target}"

    variants: list[Variant] = []
    occurrences: dict[str, int] = {s: 0 for s in STRATEGY_ORDER}
    # Cap total attempts so an all-skipping target (no peer + LLM down/over
    # budget) can't spin: at most 2 full passes over the strategy list beyond n.
    max_attempts = n + 2 * len(STRATEGY_ORDER)
    attempt = 0
    i = 0
    while len(variants) < n and attempt < max_attempts:
        strategy = STRATEGY_ORDER[i % len(STRATEGY_ORDER)]
        i += 1
        attempt += 1
        variant = _make_variant(
            strategy, target, base, occurrences[strategy],
            base_source=base_source, parent_id=parent_id, budget=budget,
        )
        if variant is None:
            continue
        occurrences[strategy] += 1
        variants.append(variant)

    if len(variants) < n:
        logger.info(
            "evolution_short_run",
            extra={"target": target, "requested": n, "produced": len(variants)},
        )
    return variants
