# Ubongo — Manual Smoke Test Playbook

This playbook is the cumulative end-to-end manual test for Ubongo. After every implementation phase is complete, walk through every section that exists. New phases append a new section.

The point: at every phase boundary, the *entire* Ubongo system should still work. New features land additively; old features must not regress.

Each section's scenarios are run in order. If a scenario fails, the phase is not complete.

## How to use

1. Make sure you're on `main` with the latest merge.
2. Set up: `uv sync`; `cp .env.example .env`; set `OPENROUTER_API_KEY`.
3. From section "Phase 0" forward, work through every section that's present.
4. Mark each scenario pass/fail in your local copy. Do not commit pass/fail marks; they're per-run.

## Phase 0 — Skeleton

*(Populated when Phase 0 is implemented.)*

Stub:

1. `uv run python -m ubongo` exits cleanly with a JSON startup line on stderr.
2. With `OPENROUTER_API_KEY` removed from `.env`, the same command exits with a clear error and rc 1.

## Phase 1 — CLI REPL + One-Shot (echo)

*(Populated when Phase 1 is implemented.)*

Stub:

1. `python -m ubongo` opens a REPL prompt.
2. Typing `hello` echoes `[architect] hello`.
3. `/casual` then `hello` echoes `[casual] hello`.
4. `/auto` then `hello` echoes `[architect] hello` (default).
5. `/exit` quits cleanly.
6. `python -m ubongo send "hi" --persona casual` prints `[casual] hi` and exits.

## Phase 2 — LLM Integration

*(Populated when Phase 2 is implemented.)*

## Phase 3 — Tone Classifier + Auto Routing

*(Populated when Phase 3 is implemented.)*

## Phase 4 — SQLite Memory + Compaction

*(Populated when Phase 4 is implemented.)*

## Phase 5 — Markdown Vault Projection

*(Populated when Phase 5 is implemented.)*

## Phase 6 — Skills + Progressive Disclosure

*(Populated when Phase 6 is implemented.)*

## Phase 7 — Minimal Outbound Queue

*(Populated when Phase 7 is implemented.)*

## Phase 8 — Master Agent

*(Populated when Phase 8 is implemented.)*

## Phase 9 — First Workers (Research + Memory)

*(Populated when Phase 9 is implemented.)*

## Phase 10 — Evaluator + Critic + Persona Agents

*(Populated when Phase 10 is implemented.)*

## Phase 11 — Coding + Execution + Repair Agents

*(Populated when Phase 11 is implemented.)*

## Phase 12 — Execution Modes (all six)

*(Populated when Phase 12 is implemented.)*

## Phase 13 — Repair Agent Activated

*(Populated when Phase 13 is implemented.)*

## Phase 14 — Risk + Confidence Scoring

*(Populated when Phase 14 is implemented.)*

## Phase 15 — Approval Gates + Sandboxing

*(Populated when Phase 15 is implemented.)*

## Phase 16 — Variant Generation

*(Populated when Phase 16 is implemented.)*

## Phase 17 — Sandboxed Evaluation + Fitness

*(Populated when Phase 17 is implemented.)*

## Phase 18 — GP Loop (autonomous)

*(Populated when Phase 18 is implemented.)*

## Phase 19 — GP Targets Expanded + Promotions

*(Populated when Phase 19 is implemented.)*

## Phase 20 — Embeddings + Graph

*(Populated when Phase 20 is implemented.)*

## Phase 21 — Bidirectional Vault Sync + Audit

*(Populated when Phase 21 is implemented.)*
