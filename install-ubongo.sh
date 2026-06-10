#!/usr/bin/env bash
#
# Ubongo bootstrap installer (macOS + Linux, incl. Raspberry Pi).
#
# This is the manual-CD entry point. Distribute it next to the bundle zip
# (ubongo-v<version>.zip). It opens the zip, places the files in your chosen
# directory, installs all dependencies, and asks for the configuration details
# it needs (your OpenRouter API key). Nothing is required beforehand except
# Python 3.11+.
#
#   ./install-ubongo.sh                 # finds ubongo-v*.zip next to this script
#   ./install-ubongo.sh path/to.zip     # explicit bundle
#   ./install-ubongo.sh --dest ~/apps   # install location (else it asks)
#   ./install-ubongo.sh --web           # also install the optional web UI
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m xx\033[0m %s\n' "$*" >&2; exit 1; }

# --- parse args -------------------------------------------------------------
ZIP=""
DEST=""
INNER_ARGS=()   # passed through to the in-bundle install.sh (e.g. --web)
while [ "$#" -gt 0 ]; do
  case "$1" in
    --web)        INNER_ARGS+=("--web") ;;
    --dest)       shift; DEST="${1:-}" ;;
    --dest=*)     DEST="${1#--dest=}" ;;
    -h|--help)    echo "Usage: ./install-ubongo.sh [bundle.zip] [--dest DIR] [--web]"; exit 0 ;;
    *.zip)        ZIP="$1" ;;
    *)            die "Unknown argument: $1 (try --help)" ;;
  esac
  shift
done

# --- prerequisites ----------------------------------------------------------
OS="$(uname -s)"
if ! command -v python3 >/dev/null 2>&1; then
  if [ "$OS" = "Darwin" ]; then
    die "python3 not found. Install Python 3.11+ first: brew install python  (or python.org)."
  else
    die "python3 not found. Install it: sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
  fi
fi

# --- locate the bundle ------------------------------------------------------
if [ -z "$ZIP" ]; then
  ZIP="$(ls "$HERE"/ubongo-v*.zip 2>/dev/null | sort | tail -1 || true)"
fi
[ -n "$ZIP" ] && [ -f "$ZIP" ] || \
  die "No bundle found. Put ubongo-v*.zip next to this script, or pass its path."
say "Bundle: $(basename "$ZIP")"

# --- choose install location ------------------------------------------------
if [ -z "$DEST" ]; then
  DEFAULT_DEST="$HOME/ubongo"
  printf 'Install Ubongo into [%s]: ' "$DEFAULT_DEST"
  read -r ANS || true
  DEST="${ANS:-$DEFAULT_DEST}"
fi
mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"

# --- extract (use python's zipfile so we never depend on `unzip`) -----------
say "Extracting into $DEST"
python3 -m zipfile -e "$ZIP" "$DEST"

APP="$(ls -d "$DEST"/ubongo-v*/ 2>/dev/null | sort | tail -1 || true)"
[ -n "$APP" ] || die "Extraction did not produce an ubongo-v* folder under $DEST."
APP="${APP%/}"

# --- run the in-bundle installer (deps + config prompts) --------------------
chmod +x "$APP"/install.sh "$APP"/start-ubongo.sh "$APP"/start-ubongo-web.sh 2>/dev/null || true
say "Setting up Ubongo in $APP"
( cd "$APP" && ./install.sh ${INNER_ARGS[@]+"${INNER_ARGS[@]}"} )

echo
say "Ubongo installed at: $APP"
echo "    Start the REPL:   cd \"$APP\" && ./start-ubongo.sh"
echo "    User manual:      $APP/docs/USER_MANUAL.md"
