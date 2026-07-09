#!/usr/bin/env bash
#
# Ubongo — live console launcher (v0.6). A streaming browser front: open the
# page, send a turn, and watch its pipeline events stream live over SSE. A full
# governed turn per message (no orchestration bypass); the stream only observes.
#
# LAN no-auth posture, like the web/MCP channels — serve it on a trusted network
# only. On the Pi, run it behind the egress envelope (ADR-0017).
#
#   ./start-ubongo-console.sh            # binds 0.0.0.0:8770
#
# Requires the optional console dependency. Install it once with either:
#   ./install.sh --console      (pip, the Pi/Ubuntu path)
#   uv sync --extra console     (uv, the dev path)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ ! -d .venv ]; then
  echo "Ubongo is not installed yet. Run:  ./install.sh --console" >&2
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if ! python -c 'import fastapi, uvicorn' >/dev/null 2>&1; then
  echo "The console dependency is not installed." >&2
  echo "Install it with:  ./install.sh --console   (or: uv sync --extra console)" >&2
  exit 1
fi

echo "Starting Ubongo live console on http://0.0.0.0:8770 (LAN, no auth)."

exec python -m ubongo console
