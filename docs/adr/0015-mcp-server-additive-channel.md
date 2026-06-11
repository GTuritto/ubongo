# 0015 — MCP server as an additive channel: no bypass, approval stays human, reads stay read-only

Status: Accepted
Date: 2026-06-11

## Context

Other agents (the Compendium project's agent, Claude Code/Desktop) need to
reach Ubongo. v0.1's channels are all human-facing (REPL, one-shot, the LAN
web page); there was no machine-facing surface. MCP is the emerging standard
for agent-to-tool connection, and Ubongo will later also want the client
direction (consuming external MCP servers like Compendium — deferred to the
next layer).

The architecture rule "new tools default to CLI scripts via constrained-bash;
first-class tools require justification" governs how *Ubongo's agents* gain
tools; it does not govern channels. A second rule does: every channel goes
through the one orchestration seam (`master.handle`) with no bypass
(ADR-0002/0003), as the web channel already proved.

## Decision

Ship Ubongo as an **MCP server** (v0.1.4, candidate 13), built as a thin
adapter over the existing seams:

- **A fourth additive channel.** `src/ubongo/mcp/service.py` is the
  channel-free core (the MCP equivalent of `oneshot.run`): `ubongo_send`
  runs `master.handle` with the queue flushed, so every MCP-driven turn is
  classified, planned, executed, governed, composed, enqueued, and persisted
  by the Memory Agent exactly like a typed one. `server.py` is the only
  module importing the `mcp` SDK and only the `ubongo mcp` entrypoint loads
  it; the SDK is an optional extra (`./install.sh --mcp`, `uv sync --extra
  mcp`), like streamlit for the web channel. The hand-rolled rule is not
  violated: it targets orchestration frameworks, and protocol clients follow
  the LiteLLM precedent.
- **Approval stays human.** MCP is non-interactive. A `require_approval`
  turn returns the canned gated message with `gated=true`; the approval
  payload is never forwarded and the gate cannot be answered over MCP.
  Approving destructive work requires a channel with a human present (REPL
  y/n/why, web Approve/Deny).
- **Reads are read-only.** `ubongo_recall` and the two resources
  (`ubongo://vault/daily/today`, `ubongo://audit`) touch no write path; the
  single-writer rule is untouched.
- **Two transports, one posture.** stdio for same-machine clients that spawn
  the process; streamable HTTP (`ubongo mcp --http`, port 8765) for LAN
  clients — **no auth, no TLS, home LAN only**, the same documented posture
  as the web UI, now the second unauthenticated LAN listener
  (docs/SECURITY.md). Service management mirrors the web channel:
  `ubongo-ctl.sh ... mcp` or `deploy/ubongo-mcp.service`.

## Consequences

- Any MCP client can now drive a full governed turn or read Ubongo's memory;
  what a caller can do is bounded by the same governance matrix as a typed
  turn, plus the harder MCP-specific rule that gates cannot be approved.
- An unattended caller can still spend model tokens via `ubongo_send`; the
  LAN-only posture is the control, as with the web page. Rate limiting is a
  conscious non-feature at this scale (one user, one LAN).
- The MCP process starts no background daemons (no GP loop, watcher, or
  authoring), mirroring one-shot and web; daemons remain the REPL's job.
- The client half (Ubongo consuming Compendium's MCP tools, where the
  tools-vs-CLI-scripts rule genuinely bites) is the next layer's decision,
  not prejudged here.
