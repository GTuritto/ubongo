# 0004 — Governance decision matrix + human approval gate

Status: Accepted
Date: backfilled 2026-06-04 (decision dates to Phases 14–15)

## Context

An agent that can run shell commands and act on the user's machine needs a gate between "decided" and "done." A binary allow/deny is too blunt — some turns are safe to auto-answer, some need clarification, some need explicit sign-off, and some should be refused. The thresholds must be reviewable and not buried in code.

## Decision

A priority-ordered decision matrix in `governance/decision.py`, configured by `config/governance.yaml`, returning one of `auto | ask_clarification | require_approval | reject`. Three scorers feed it: `risk` (the higher of the classifier's rating and a destructive-keyword scan), `confidence` (the Evaluator's score, classifier confidence as fallback), and `reversibility` (irreversible when the Execution agent or an irreversible skill runs). Rules in order: destructive → require_approval; high + irreversible → require_approval; low evaluator confidence → reject; low-confidence command → ask_clarification; else auto. `require_approval` is an interactive `y/n/why` REPL gate; one-shot is non-interactive (`rc=1`). The decision and scored signals persist to `governance_decisions`.

## Consequences

- Safety before quality before clarity, with every threshold in config (nothing hardcoded); `/policy` prints the live matrix.
- The Execution sandbox is the enforcement backstop (see ADR 0005); governance decides whether to even attempt.
- The same `confidence` signal the Evaluator produces drives both the borderline-Critic re-dispatch and the reject floor.

References: `UBONGO_BUILD.md` Phases 14–15; `src/ubongo/governance/*`, `config/governance.yaml`; `docs/SECURITY.md`.
