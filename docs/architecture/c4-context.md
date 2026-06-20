# C4 Level 1 — System Context

Ubongo is a personal, intent-routed AI mind for a single user, running locally as a
CLI. It orchestrates a fleet of worker agents across multiple LLM calls, gates
risky actions, and persists everything it does.

```mermaid
C4Context
  title System Context - Ubongo

  Person(user, "Giuseppe", "The single user. Talks to Ubongo through the CLI (REPL + one-shot).")

  System(ubongo, "Ubongo", "Local multi-agent AI mind. Classifies each turn, dispatches worker agents, gates risk, and persists every run.")

  System_Ext(llm, "LLM Providers", "Anthropic Claude and other models, reached through the LiteLLM gateway.")
  System_Ext(obsidian, "Obsidian", "External knowledge app that reads the Markdown vault Ubongo projects.")
  System_Ext(os, "Local OS / Shell", "Host machine. Allowlisted read-mostly commands run here via the sandbox.")

  Rel(user, ubongo, "Sends messages, runs slash commands, approves actions", "CLI / stdin")
  Rel(ubongo, user, "Returns composed responses, approval prompts", "CLI / stdout")
  Rel(ubongo, llm, "Sends classification + agent prompts, receives completions", "HTTPS")
  Rel(ubongo, obsidian, "Projects daily notes and memory", "Markdown files on disk")
  Rel(ubongo, os, "Runs allowlisted commands", "subprocess, shell=False")

  UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```

## Notes

- **One user, one machine.** v0.1 is CLI-only; Telegram is v0.2 and would be an
  additive transport, not a change to this context.
- **LLM access is centralized.** Every model call goes through one gateway
  (`llm.py`, LiteLLM), so provider choice is configuration, not code.
- **The vault is an output, not a dependency.** Ubongo writes Markdown; Obsidian
  is one possible reader. Ubongo runs fine with no Obsidian installed.
- **Shell access is constrained by design.** The only path to the host OS is the
  sandbox: explicit command allowlist, no shell metacharacters, no path
  traversal, repo-root cwd, 10s timeout.
