# 0005 — Shell-execution safety enforced in code, not SKILL.md

Status: Accepted
Date: backfilled 2026-06-04 (decision dates to Phases 11, 15)

## Context

The Execution Agent runs shell commands via a `constrained-bash` skill. A SKILL.md body is markdown the LLM side reads — the model can be prompted, jailbroken, or simply ignore it. Anything that determines what actually runs on the user's machine cannot depend on the LLM honoring instructions in a prompt.

## Decision

The entire shell-execution safety contract lives in `src/ubongo/sandbox.py`, code the LLM cannot rewrite — not in the SKILL.md body (which is metadata + a prompt template only). The contract: an allowlist resolved to absolute program paths, no shell metacharacters anywhere, no path traversal, a filesystem allowlist (absolute-path arguments must resolve inside the repo tree), `shell=False`, an **empty child `PATH`** (the child cannot spawn helpers by bare name), repo-root cwd, a 10s timeout, and output caps. The seam stays in one module across phases.

## Consequences

- The security boundary is auditable in one file and independent of model behavior; `docs/SECURITY.md` documents the contract and its known v0.1 limits (no OS-level isolation; network is governed by the allowlist, not blocked).
- New tools default to CLI scripts invoked through constrained-bash rather than first-class tool definitions, keeping the privileged surface small.
- v0.1 accepts no kernel/container isolation; the allowlist + argument rules are the boundary.

References: `UBONGO_BUILD.md` Phases 11/15; `CLAUDE.md` (shell-execution safety rule); `src/ubongo/sandbox.py`, `docs/SECURITY.md`.
