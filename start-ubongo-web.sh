#!/usr/bin/env bash
#
# Ubongo — web UI launcher (Streamlit). A local, self-hosted chat page for
# driving Ubongo from another device on your home network (e.g. a tablet).
# Binds the LAN by default so the tablet can reach it; no TLS, no auth — home
# LAN only (see docs/SECURITY.md and src/ubongo/web/app.py).
#
#   ./start-ubongo-web.sh
#   UBONGO_WEB_PORT=9000 ./start-ubongo-web.sh
#
# Requires the optional web dependency. Install it once with either:
#   ./install.sh --web          (pip, the Pi/Ubuntu path)
#   uv sync --extra web         (uv, the dev path)
#
# Then open http://<this-machine-ip>:8501 on your tablet.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

ADDR="${UBONGO_WEB_ADDR:-0.0.0.0}"
PORT="${UBONGO_WEB_PORT:-8501}"

if [ ! -d .venv ]; then
  echo "Ubongo is not installed yet. Run:  ./install.sh --web" >&2
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if ! python -c 'import streamlit' >/dev/null 2>&1; then
  echo "The web UI dependency (streamlit) is not installed." >&2
  echo "Install it with:  ./install.sh --web   (or: uv sync --extra web)" >&2
  exit 1
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
echo "Starting Ubongo web UI on http://${ADDR}:${PORT}"
[ -n "${IP:-}" ] && echo "  reach it from your tablet at: http://${IP}:${PORT}"

exec python -m streamlit run src/ubongo/web/app.py \
  --server.address "$ADDR" \
  --server.port "$PORT" \
  --server.headless true \
  --browser.gatherUsageStats false
