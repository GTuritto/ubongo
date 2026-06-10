#!/usr/bin/env bash
#
# Ubongo installer — macOS + Linux (Debian/Ubuntu, incl. Raspberry Pi 5 / ARM64).
#
# Creates a local Python virtualenv (./.venv), installs Ubongo and its
# dependencies, sets up the data + vault folders, and configures your
# OpenRouter API key. Re-runnable (idempotent). Run in place after the bundle is
# unzipped (the install-ubongo.sh bootstrap does the unzip + calls this).
#
#   ./install.sh
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

OS="$(uname -s)"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m xx\033[0m %s\n' "$*" >&2; exit 1; }

# OS-aware hint for installing/repairing Python (macOS uses Homebrew/python.org;
# Debian/Ubuntu uses apt).
pyhint() {
  if [ "$OS" = "Darwin" ]; then
    echo "macOS: brew install python   (or download from https://www.python.org/downloads/)"
  else
    echo "Debian/Ubuntu: sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
  fi
}

# --web also installs the optional Streamlit web UI (the tablet chat page).
WITH_WEB=0
for arg in "$@"; do
  case "$arg" in
    --web) WITH_WEB=1 ;;
    -h|--help) echo "Usage: ./install.sh [--web]"; exit 0 ;;
    *) die "Unknown option: $arg (try --web)" ;;
  esac
done

say "Installing Ubongo into: $APP_DIR"
[ "$WITH_WEB" -eq 1 ] && say "Including the optional web UI (Streamlit)."

# --- 1. Python >= 3.11 ------------------------------------------------------
command -v python3 >/dev/null 2>&1 || \
  die "python3 not found. Install Python 3.11+ first. $(pyhint)"

PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
PYMAJ=${PYV%%.*}; PYMIN=${PYV##*.}
if [ "$PYMAJ" -lt 3 ] || { [ "$PYMAJ" -eq 3 ] && [ "$PYMIN" -lt 11 ]; }; then
  die "Python $PYV found; Ubongo needs >= 3.11. $(pyhint)"
fi
say "Python $PYV detected ($OS) — OK"

python3 -c 'import venv' 2>/dev/null || \
  die "The Python 'venv' module is missing. $(pyhint)"

# --- 2. virtualenv ----------------------------------------------------------
if [ ! -d .venv ]; then
  say "Creating virtualenv (.venv)…"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# --- 3. dependencies --------------------------------------------------------
say "Installing dependencies (a few minutes on a Pi the first time)…"
python -m pip install --upgrade pip wheel >/dev/null
if [ "$WITH_WEB" -eq 1 ]; then
  python -m pip install -e ".[web]"
else
  python -m pip install -e .
fi

# --- 4. sqlite-vec sanity (optional; recall degrades gracefully) ------------
if python -c 'import sqlite_vec' 2>/dev/null; then
  say "sqlite-vec available — semantic recall enabled."
else
  warn "sqlite-vec is not importable on this platform."
  warn "Ubongo will run fine in recency-only mode (no semantic recall). This is fully supported."
fi

# --- 5. data + vault folders ------------------------------------------------
mkdir -p data vault/daily vault/system
[ -f vault/.gitkeep ] || : > vault/.gitkeep

# --- 6. .env / OpenRouter API key ------------------------------------------
if [ ! -f .env ]; then
  cp .env.example .env
  say "Created .env from .env.example"
fi

KEY=$(grep -E '^OPENROUTER_API_KEY=' .env | cut -d= -f2- || true)
if [ -z "${KEY:-}" ]; then
  echo
  warn "Ubongo needs an OpenRouter API key — get one free at https://openrouter.ai/keys"
  read -r -p "Paste your OPENROUTER_API_KEY now (or press Enter to add it later): " INPUT || true
  if [ -n "${INPUT:-}" ]; then
    python - "$INPUT" <<'PYEOF'
import re, sys, pathlib
key = sys.argv[1].strip()
p = pathlib.Path(".env"); t = p.read_text()
t = re.sub(r'^OPENROUTER_API_KEY=.*$', f'OPENROUTER_API_KEY={key}', t, flags=re.M)
p.write_text(t)
PYEOF
    say "API key saved to .env"
  else
    warn "Edit .env and set OPENROUTER_API_KEY before starting Ubongo."
  fi
fi

# --- 7. verify (only if a key is set; makes NO paid API call) --------------
KEY=$(grep -E '^OPENROUTER_API_KEY=' .env | cut -d= -f2- || true)
if [ -n "${KEY:-}" ]; then
  say "Verifying install (cold start — no model call)…"
  if printf '/exit\n' | python -m ubongo >/dev/null 2>&1; then
    say "Cold start OK."
  else
    warn "Cold start returned non-zero. Double-check OPENROUTER_API_KEY in .env."
  fi
fi

echo
say "Install complete."
echo "    Start the REPL:   ./start-ubongo.sh"
echo "    One-shot:         ./start-ubongo.sh send \"hello\" --persona casual"
if [ "$WITH_WEB" -eq 1 ]; then
  echo "    Web UI (tablet):  ./start-ubongo-web.sh   then open http://<this-ip>:8501"
else
  echo "    Web UI (tablet):  re-run ./install.sh --web, then ./start-ubongo-web.sh"
fi
echo "    User manual:      docs/USER_MANUAL.md"
