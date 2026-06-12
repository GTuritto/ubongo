# 0016 — External tools behind one Connector seam; the first-class tool layer stays unjustified

Status: Accepted
Date: 2026-06-12

## Context

v0.1.4 made Ubongo reachable over MCP; nothing let Ubongo reach out. The
sandbox rightly blocks the network for shell tools, so external capability
(the Compendium project, any MCP server) needed a governed path. Three shapes
were considered:

1. **A Connector worker agent** that owns MCP sessions and returns Findings.
2. **A first-class tool layer** — MCP tools as function definitions on every
   LLM agent's model calls, executed mid-turn by the envelope.
3. **A CLI bridge** through constrained-bash (an `mcp-call` script on the
   sandbox allowlist).

The architecture rule "new tools default to CLI scripts via constrained-bash;
first-class tools require justification" governs this choice directly.

## Decision

Shape 1 (shipped as v0.1.5, candidate 20):

- **One seam.** `agents/connector.py` is the ninth worker (`composer=False`):
  it discovers tools on the servers declared in `settings.yaml::mcp.servers`,
  plans calls with its model (JSON plan, Evaluator-style tolerant parse),
  executes them through `mcp/client.py` (per-turn sessions, stdio + streamable
  HTTP, sync facade, lazy SDK import), and returns results as Findings. The
  Memory Agent remains the only writer; the persona composes.
- **The first-class tool layer is deferred, not granted.** The justification
  bar was not met: it would rewrite the model-call envelope, spread external
  side effects across the whole fleet, and dissolve the single point where
  governance can reason about "this turn touched the outside world."
- **The CLI bridge was rejected** because it would carve a network hole into
  the sandbox — weakening its strongest guarantee to satisfy a rule whose
  purpose that guarantee serves.
- **Opt-in routing.** `connector_session` is declared but not auto-routed
  (`/mode connector_session`), the `execution_session` precedent; auto-routing
  is a later, evidence-based change.
- **Governance.** A connector workflow is **irreversible** (external calls
  happened), and turn risk escalates to at least the highest declared `risk:`
  among enabled servers — low-risk read-only servers stay `auto`, a high-risk
  server hits the existing `irreversible_high_risk` approval row. No new
  matrix rules; new inputs only. Tool calls log structured events and append
  `[mcp]` audit rows.
- **Secrets posture.** Server config carries no secrets; an `env:` map names
  host environment variables resolved at connect time (`.env` stays the only
  secret store).

## Consequences

- What leaves the machine is the tool arguments the Connector's model plans —
  a real consideration on top of the LAN-trust posture; documented in
  SECURITY.md. Per-server `risk` + `enabled` are the operator's controls.
- Like the Execution agent, calls happen at execute time while the gate
  governs delivery: a high-risk server's result needs approval, but true
  pre-execution gating of individual calls is future work (tool-level
  allowlists were considered and deferred until Compendium's tool names
  exist).
- A dead or empty server degrades, never breaks: honest `ok=True` findings
  for no-SDK/no-servers/no-tools, and `connector_mcp_error` failures enter the
  Repair ladder (peer: architect), so the turn answers unaided.
- The loop-back pattern (Ubongo's own MCP server as the client's peer) gives
  the full outbound path a test and smoke story with no external dependency.
