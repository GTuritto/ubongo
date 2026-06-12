# Phase 20 — MCP client: the Connector agent (v0.1.5)

Branch: `improve/20-mcp-client`. Design approved in-session 2026-06-12
(brainstormed: Connector worker agent; `/mode` opt-in like the
`execution_session` precedent; governance = irreversible + per-server risk).

## Problem

Ubongo can be called over MCP (v0.1.4) but cannot call anyone: no agent can
reach an external service (the sandbox rightly blocks the network). The user's
Compendium project — and any other MCP server — is unreachable. The
architecture rule "first-class tools require justification" rules out
scattering tool definitions across the fleet; external capability needs one
governed seam.

## Solution

### 20a — the session layer: `mcp/client.py`

Joins the existing `mcp/` package (SDK behind the optional extra; this module
imported lazily by the connector). Reads the new `mcp:` settings block:
per-server `name`, `transport: stdio|http`, `command`+`args` or `url`,
`risk: low|medium|high`, `enabled: bool`, optional `env: {VAR: ENV_NAME}`
(values resolved from the environment at connect time — config carries no
secrets). API (sync facade, `asyncio.run` per operation — safe in the runner's
worker threads):

- `servers() -> list[ServerConfig]` (enabled only; parse-tolerant: a bad entry
  logs + is skipped, never crashes a turn)
- `tool_catalog() -> list[ToolInfo]` — name/description/schema per server;
  per-turn fetch (no daemon, no persistent sessions in v0.1.5)
- `call_tool(server, tool, arguments, timeout_s=20) -> ToolResult`
  (`ok`, text content, `error`)
- `max_enabled_risk() -> str | None` — for governance.

### 20b — the Connector agent: `agents/connector.py`

Ninth worker (`name="connector"`, `composer=False`, model
`models.connector` → `models.default`). `run()`:

1. No SDK / no enabled servers → honest `ok=True` finding saying so (the
   workflow proceeds; architect answers unaided).
2. Build the tool catalog; LLM plans calls as JSON
   (`{"calls": [{server, tool, arguments}], "reason"}`) via the shared
   envelope (`run_agent_llm` + `on_success` parse, Evaluator-style tolerant).
3. Execute the plan through `client.call_tool`; format results as a Finding
   (server/tool/result blocks); `metadata` carries `mcp_calls` +
   `max_server_risk`. Plan-parse failure or all-calls-failed →
   `ok=False, error="connector_mcp_error"` so the Repair ladder applies
   (peer replacement: `connector: architect` in settings — a dead Compendium
   degrades to a normal turn).

### 20c — workflow + governance

- `workflows.yaml`: `connector_session` = `agents: [connector, architect]`,
  `mode: sequential`, `evaluate: true`. Declared, **not auto-routed**
  (`/mode connector_session`), the `execution_session` precedent.
- `governance/reversibility.py`: `connector` joins `execution` as
  irreversible.
- Risk: in the decide path, a workflow containing `connector` escalates risk
  to `max(classifier risk, mcp.max_enabled_risk())`. Low-risk Compendium →
  `low + irreversible` → `auto`; a high-risk server → `require_approval` via
  the existing matrix row. Honest note (same asymmetry as the Execution
  agent): the gate governs delivery; per-call safety rests on per-server
  `risk` + `enabled` config. Tool calls log structured events and append an
  `[mcp]` row to the unified audit.

### 20d — tests

- `tests/test_mcp_client.py`: config parsing (good/bad/disabled entries, env
  resolution), catalog + call against an **in-process FastMCP server** (the
  v0.1.4 helper — no network), timeout + error shaping.
- `tests/test_agents_connector.py`: stubbed client + stubbed model — plan
  parse, execution, no-server finding, no-tool plan, error finding.
- Governance: connector ⇒ irreversible; risk escalation with a high-risk
  server config.

### 20e — smoke

- `scripts/smoke.sh` (guarded on the extra): config parse, `/mode list` shows
  `connector_session`, and a **loop-back live check**: start Ubongo's own MCP
  server via ctl, configure it as a client server pointing at
  `http://127.0.0.1:8765/mcp`, run a `/mode connector_session` turn that calls
  `ubongo_recall` through the connector — the full client path with no
  external dependency.
- Playbook section C.1–C.x (incl. Compendium-specific manual rows for when it
  exists).

### 20f — docs (merging publishes v0.1.5)

VERSION + pyproject + lock → 0.1.5; CHANGELOG; **ADR-0016** (external tools
behind one Connector seam; first-class tool layer deferred-not-granted;
LAN-trust posture for outbound calls) + index; CONTEXT.md **Connector agent**
entry; README + USER_MANUAL (`mcp:` config example with Compendium commented
out); SECURITY.md (what leaves the machine: the tool arguments the model
plans); C4 fleet line; STATE/STATUS. Plus a hand-off prompt for the
Compendium session to grow the matching MCP server.

## Behavior to preserve

- No SDK / no config → every existing workflow and test untouched; the
  connector agent registers but reports honestly when invoked.
- Sandbox posture unchanged (network stays blocked for `/exec`; MCP egress
  lives only in the client layer).
- Single-writer rule: the connector returns Findings only.

## Done when

- Suite green (with and without the extra); smoke gate green including the
  loop-back; a live `/mode connector_session` turn answers using a tool
  result; PR ready; user merges (pipeline publishes v0.1.5).
