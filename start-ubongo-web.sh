#!/usr/bin/env bash
#
# Launch the Ubongo web UI (Streamlit) bound to the local network so a tablet on
# the same Wi-Fi can reach it. No TLS, no auth — home LAN only (see the security
# note in src/ubongo/web/app.py). Override host/port via env vars.
#
#   ./start-ubongo-web.sh
#   UBONGO_WEB_PORT=9000 ./start-ubongo-web.sh
#
# Requires the optional web dependency:
#   uv sync --extra web      (or: uv pip install -e ".[web]")
#
# Then open http://<this-machine-ip>:8501 on your tablet.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

ADDR="${UBONGO_WEB_ADDR:-0.0.0.0}"
PORT="${UBONGO_WEB_PORT:-8501}"

exec uv run --extra web streamlit run src/ubongo/web/app.py \
  --server.address "$ADDR" \
  --server.port "$PORT" \
  --server.headless true \
  --browser.gatherUsageStats false
