#!/usr/bin/env bash
#
# Ubongo — Signal channel launcher. Drive Ubongo from Signal: a full governed
# turn per message, with approve-later for gated turns (/approve <id>, /pending,
# /grants). Auth is real — only the E.164 numbers in
# settings.yaml::signal.allowed_numbers may drive it (empty = deny all).
#
# Unlike Telegram there is NO token here: the transport is a locally-run
# signal-cli daemon (its own on-disk keystore holds the credential). This script
# runs the Ubongo *channel*; the signal-cli daemon is a separate process — start
# it first (see docs/signal-setup.md), or use deploy/ubongo-signal-cli.service.
#
# Cloud-relayed like Telegram (messages transit Signal's servers). On the Pi,
# run it behind the egress envelope (ADR-0017): Signal's hosts must be in
# /etc/ubongo/egress.hosts (see deploy/envelope/egress.hosts). ADR-0024.
#
#   ./start-ubongo-signal.sh
#
# There is no pip extra (the client is pure stdlib); the only prerequisite is the
# external signal-cli daemon. Register a dedicated number once per
# docs/signal-setup.md, then start the daemon on the socket named in settings.yaml.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ ! -d .venv ]; then
  echo "Ubongo is not installed yet. Run:  ./install.sh" >&2
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if ! command -v signal-cli >/dev/null 2>&1; then
  echo "Note: signal-cli is not on PATH. The channel needs a running signal-cli" >&2
  echo "daemon (see docs/signal-setup.md). Continuing — the entrypoint will report" >&2
  echo "if the daemon socket is unreachable." >&2
fi

echo "Starting Ubongo Signal channel. Authorized numbers only."

exec python -m ubongo signal
