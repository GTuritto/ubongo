# 0006 — GP self-improvement: variant/lineage/fitness, approved-not-autonomous

Status: Accepted
Date: backfilled 2026-06-04 (decision dates to Tier 5, Phases 16–19)

## Context

Ubongo aims to improve its own prompts and configuration over time. The risk of a self-modifying system is obvious: it could drift, regress, or promote a change the user never wanted. The design must let the system explore and rank improvements while keeping a human firmly in the loop for anything that changes production behavior.

## Decision

Model improvement as genetic programming over a persisted **lineage**. Per target, the system **generates** a generation of **variants**, **evaluates** each to a **fitness** (a cohort-normalized weighted sum over success/cost/latency/hallucination/user-correction), keeps the top-K **survivors**, and seeds the next generation from the champion (cross-generation lineage via `parent_id`). A background **GP loop** (`EvolutionLoop`) runs cycles continuously but is **throttled** (a rolling-hour call budget), **paced** (`evolution.cron`), and **starts paused**. Crucially, **promotion is approved, not autonomous**: the loop only *proposes* (`pending_promotions`) when a champion beats the active baseline by a margin; the user approves via `/improvements`. Evaluation is side-effect-free; all progress lives in SQLite so a restart resumes.

## Consequences

- The system can evolve indefinitely in the background at bounded cost without ever changing behavior on its own.
- Everything is auditable: lineage, evaluations, cycle log, and a promotion audit at `vault/system/evolution-audit.md`.
- Retry-config fitness is the weak spot — see ADR 0007.

References: `UBONGO_BUILD.md` Tier 5; `Plans/phase-16..19-*.md`; `src/ubongo/evolution/*`.
