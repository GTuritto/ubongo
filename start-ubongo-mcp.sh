#!/usr/bin/env bash
#
# Ubongo — MCP server launcher (streamable HTTP). Lets other agents on your
# home network (e.g. the Compendium project, or Claude on another machine)
# call Ubongo over MCP: run a full governed turn (ubongo_send) or read its
# memory (ubongo_recall + read-only resources). No auth, no TLS — home LAN
# only, same posture as the web UI (see docs/SECURITY.md and ADR-0015).
#
#   ./start-ubongo-mcp.sh
#   UBONGO_MCP_PORT=9765 ./start-ubongo-mcp.sh
#
# Local clients that spawn their own server (Claude Code/Desktop on this
# machine) should use stdio instead — point them at:  python -m ubongo mcp
#
# Requires the optional mcp dependency. Install it once with either:
#   ./install.sh --mcp          (pip, the Pi/Ubuntu path)
#   uv sync --extra mcp         (uv, the dev path)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

ADDR="${UBONGO_MCP_ADDR:-0.0.0.0}"
PORT="${UBONGO_MCP_PORT:-8765}"

if [ ! -d .venv ]; then
  echo "Ubongo is not installed yet. Run:  ./install.sh --mcp" >&2
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if ! python -c 'import mcp' >/dev/null 2>&1; then
  echo "The MCP dependency is not installed." >&2
  echo "Install it with:  ./install.sh --mcp   (or: uv sync --extra mcp)" >&2
  exit 1
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
echo "Starting Ubongo MCP server on http://${ADDR}:${PORT}/mcp"
[ -n "${IP:-}" ] && echo "  reach it from the LAN at: http://${IP}:${PORT}/mcp"

exec python -m ubongo mcp --http --addr "$ADDR" --port "$PORT"
