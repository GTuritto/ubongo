# 0009 — Classifier determinism + routing completeness

Status: Accepted
Date: 2026-06-04 (PR #12, merge `beaf230`)

## Context

A cumulative smoke run found a technical design question ("help me design a circuit breaker") reaching the casual persona ~2/3 of the time. Diagnosis (6 live classify runs) showed three compounding causes, none a regression: (1) `llm.complete()` never forwarded a `temperature`, so the classifier ran at the model's ~1.0 default — a coin-flip across runs; (2) the intent taxonomy listed `[technical, casual, work, research, coding, other]` with no definitions, so the 7B classifier couldn't separate technical/work/coding; (3) `routing.yaml` only routed `work` when `task_type: command`, so `work` + `question` fell through to `casual_reply`.

## Decision

- Thread an optional `temperature` through `llm.complete` (omitted by default → other callers unchanged) and pin the classifier to `temperature=0`.
- Add one-line definitions to the intent taxonomy plus a tie-breaker: prefer `technical` over `work` for design/engineering questions.
- Close the routing gap with an `intent: work` catch-all → `quick_action` (operator), so a `work` turn never silently lands on casual.

## Consequences

- Classification is stable and correct on the tested prompts (verified deterministic, 4/4 live): "design a circuit breaker" → technical, "write a function…" → coding, "what's left on the sprint" → work → quick_action.
- A classifier should always run at temperature 0; this is now the norm. Other model calls keep the provider default.
- The 7B classifier model is still the limiting factor for genuinely ambiguous prompts; definitions reduce but don't eliminate that.

References: PR #12; `src/ubongo/{llm,classifier}.py`, `config/routing.yaml`.
