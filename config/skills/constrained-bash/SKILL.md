---
name: constrained-bash
description: Run a single shell command from a small allowlist (ls, cat, grep, git, python, pytest, sqlite3, etc.) inside the repo root. v0.1; sandboxing is minimal — Phase 15 will harden it.
risk: medium
reversibility: irreversible
default_persona: operator
prompts:
  run: prompts/run.md
---

The constrained-bash skill lets the Execution Agent run one shell command under a static allowlist. v0.1 enforcement lives in `src/ubongo/sandbox.py`: shlex parse, allowlist check, restricted PATH, repo-root cwd, 10s default timeout, no pipes or redirects, no path traversal.

This file is metadata + a prompt template; the actual safety contract is in code that the LLM cannot rewrite. Phase 15 will broaden the allowlist behind the approval gate and add filesystem allowlists + env scrubbing.
