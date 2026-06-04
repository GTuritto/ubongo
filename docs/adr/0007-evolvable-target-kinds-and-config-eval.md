# 0007 — Evolvable target kinds (prompt vs config) + side-effect-free config evaluation

Status: Accepted
Date: backfilled 2026-06-04 (decision dates to Phase 19)

## Context

Through Phase 18 the evolution machinery only mutated persona prompts: mutate text, generate a response, judge it. Phase 19 expands targets to routing rules, per-workflow tool chains, and the Repair retry config. These are config, not prompts — the prompt-shaped generator and evaluator don't apply. The question was how to evaluate a config variant: a cheap per-kind proxy, or run the real pipeline and judge the responses. (The user chose the latter when the trade-off was surfaced.)

## Decision

- Introduce a target **kind**: `prompt` (`persona:*`) vs `config` (`routing:default`, `toolchain:<wf>`, `retry:repair`). `variant_text` (already TEXT) holds an alternate body or a serialized config; `apply_variant` parses and **validates** config variants (workflows exist, agents registered, retry keys known) and rejects malformed ones.
- Config variants are **deterministic, validated structural mutations** (no LLM), so generation is robust and free.
- **Config evaluation runs the real pipeline** (classify → route → execute) under an in-memory `router.config_override`, via a **side-effect-free isolated executor** (no `agent_runs`/events/Repair/governance/vault/queue), then scores the produced responses with the same 3-signal judge. One fitness function across kinds.
- **Retry is the exception**: scored by a documented **structural proxy** (sane attempt cap + peer coverage), because offline samples cannot induce the failures retry exists to handle.

## Consequences

- All three config families are evolvable, promotable, and live-swappable with a single fitness model.
- Config evaluation costs a full multi-agent turn per sample (vs one generation for prompts); bounded hard by `samples_per_eval` and the call budget.
- Retry fitness does not reflect real recovery quality — a fault-injection harness is a deliberate follow-up, not v0.1.

References: `Plans/phase-19-promotions.md`; `src/ubongo/evolution/{targets,generator,sandbox}.py`, `src/ubongo/router.py` (`config_override`).
