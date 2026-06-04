#!/usr/bin/env bash
#
# Ubongo v0.1 — launcher.
#
#   ./start-ubongo.sh                         # interactive REPL
#   ./start-ubongo.sh send "hello"            # one-shot, default persona
#   ./start-ubongo.sh send "hi" --persona casual
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ ! -d .venv ]; then
  echo "Ubongo is not installed yet. Run:  ./install.sh" >&2
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate
exec python -m ubongo "$@"
