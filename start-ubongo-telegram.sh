#!/usr/bin/env bash
#
# Ubongo — Telegram bot launcher (long-poll). Lets you drive Ubongo from your
# phone: a full governed turn per message, with approve-later for gated turns
# (/approve <id>, /pending, /grants). Auth is real here — only the numeric user
# ids in settings.yaml::telegram.allowed_user_ids may drive it (empty = deny
# all). The bot token is a secret: set TELEGRAM_BOT_TOKEN in .env.
#
# This is the first cloud-relayed channel (messages transit Telegram's servers).
# On the Pi, run it behind the egress envelope (ADR-0017): api.telegram.org must
# be in /etc/ubongo/egress.hosts. See docs/SECURITY.md and ADR-0020.
#
#   ./start-ubongo-telegram.sh
#
# Requires the optional telegram dependency. Install it once with either:
#   ./install.sh --telegram     (pip, the Pi/Ubuntu path)
#   uv sync --extra telegram    (uv, the dev path)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ ! -d .venv ]; then
  echo "Ubongo is not installed yet. Run:  ./install.sh --telegram" >&2
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if ! python -c 'import httpx' >/dev/null 2>&1; then
  echo "The Telegram dependency is not installed." >&2
  echo "Install it with:  ./install.sh --telegram   (or: uv sync --extra telegram)" >&2
  exit 1
fi

echo "Starting Ubongo Telegram bot (long-poll). Authorized users only."

exec python -m ubongo telegram
