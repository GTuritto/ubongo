# Phase 13 — MCP server channel (v0.1.4)

Branch: `improve/13-mcp-server`. Approved in-session 2026-06-11 (brainstormed:
client+server support, server first; surface = turn + read-only memory;
transports = stdio + streamable HTTP; Approach A — thin adapter over the
existing seams via the official `mcp` SDK as an optional extra).

## Problem

Ubongo cannot be reached by other agents. External MCP clients (Claude
Code/Desktop, the Compendium project's agent) have no way to drive a turn or
query memory; the only channels are human-facing (REPL, one-shot, web page).
The MCP client half (Ubongo consuming Compendium's tools) is v0.1.5, out of
scope here.

## Solution

A fourth additive channel: `src/ubongo/mcp/`, split per the additive-channel
discipline so the turn logic is testable without the SDK.

### 13a — package + entrypoint

- `mcp/service.py` (imports NO mcp SDK): the channel-free core.
  - `bootstrap()` — config + logging once, resolves the `UBONGO_PROFILE` knob
    (mirrors `web/turn.bootstrap`); starts NO background daemons.
  - `send_turn(message, persona="architect", auto=False) -> dict` — validates
    persona against `VALID_PERSONAS`, calls `master.handle` exactly like
    `oneshot.run`, flushes the queue, returns `{text, ok, persona, gated,
    requires_user_decision}`. A `require_approval` turn returns the canned
    gated message with `gated=True` — approval is never possible over MCP.
    CPU profiling wraps the call when the knob includes cpu (report logged).
  - `recall_view(query="") -> dict` — read-only `store.recall`, returns
    `{summary, recency, semantic}` with `#id role: text` entries.
  - `daily_note_text()` / `audit_text()` — read-only file reads via
    `vault.daily_note_path(today)` and `vault.audit_tail()`.
- `mcp/server.py` (the only module importing the SDK): builds a FastMCP app —
  tools `ubongo_send`, `ubongo_recall`; resources `ubongo://vault/daily/today`,
  `ubongo://audit` — each delegating to `service`. `run(http=False, port=8765,
  addr="0.0.0.0")` selects stdio vs streamable HTTP.
- `__main__.py`: new `mcp` subcommand (`--http`, `--port`, `--addr`); imports
  `mcp.server` lazily inside the branch; `ImportError` exits 1 with the
  friendly hint (`./install.sh --mcp` / `uv sync --extra mcp`), mirroring the
  web launcher's streamlit check.
- `pyproject.toml`: `[project.optional-dependencies] mcp = ["mcp>=1.2"]`
  (added with `uv add --optional mcp`), lockfile updated. CI syncs
  `--all-extras` so the SDK-backed tests run there.

### 13b — service tooling

- `start-ubongo-mcp.sh` launcher (mirrors the web one: venv check, SDK check
  with install hint, `exec python -m ubongo mcp --http`, `UBONGO_MCP_PORT`).
- `ubongo-ctl.sh` generalizes: `start|stop|restart|status [web|mcp]`, default
  `web` (existing usage untouched). Per-service pidfile/log
  (`data/ubongo-<svc>.pid|.log`).
- `deploy/ubongo-mcp.service` systemd unit mirroring the web unit.
- `package.sh` ships the new launcher (cp + chmod); `install-ubongo.sh` chmod
  list gains it; `install.sh` gains `--mcp` (parallel to `--web`).

### 13c — tests (`tests/test_mcp_service.py`, `tests/test_mcp_server.py`)

- service (offline, no SDK import): happy turn / gated turn /
  repair-exhausted / bad persona with `master.handle` monkeypatched; recall on
  seeded + empty db; resource reads incl. missing daily note.
- server (`pytest.importorskip("mcp")`): in-memory FastMCP `Client` lists both
  tools + both resources and round-trips `ubongo_recall` offline.

### 13d — smoke

- `scripts/smoke.sh`: deterministic MCP checks guarded on SDK availability
  (mirrors the streamlit guard) — `ubongo mcp` rejects cleanly without the
  SDK; with it, an HTTP server starts via ctl and answers, then stops.
- Playbook: new "Post-v0.1 — MCP server" section (M.1–M.x): stdio handshake
  from a real client config, HTTP cycle, gated-turn behavior, resources.

### 13e — docs (same candidate; merging publishes v0.1.4)

- `VERSION` + `pyproject` -> 0.1.4; CHANGELOG entry.
- ADR-0015: MCP server as an additive channel — no bypass, LAN no-auth
  posture (same as web), approval stays human, resources read-only. Index row.
- CONTEXT.md glossary: **MCP channel** entry.
- README: "Connect via MCP" section (Claude Code config JSON + LAN/Compendium
  example); USER_MANUAL section; SECURITY.md (second unauthenticated LAN
  listener, same rules); C4 containers gain the channel box; STATE/STATUS
  updated.

## Behavior to preserve

- `ubongo-ctl.sh start|stop|restart|status` with no service argument behaves
  exactly as today (web).
- Core install without the extra: `import ubongo` and every existing channel
  untouched; the suite passes with the SDK absent (skips).
- Single-writer rule: MCP adds no write path; gated turns write the same
  governance rows as one-shot.

## Done when

- `pytest -q` green (with and without the SDK installed).
- Live: `ubongo mcp --http` answers an MCP `tools/list` + `ubongo_recall`
  call; ctl cycle works for `mcp`; stdio mode handshakes.
- Full smoke green; PR ready; user merges (pipeline publishes v0.1.4).

## Estimated size

~280 LOC src + ~80 shell/unit + ~160 tests + docs.
