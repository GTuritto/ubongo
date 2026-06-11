#!/usr/bin/env bash
#
# Ubongo — web service control. Manages the Streamlit web UI as a background
# service with a pidfile and a log file (the interactive REPL stays foreground;
# you leave it with /exit).
#
#   ./ubongo-ctl.sh start     # background the web UI (start-ubongo-web.sh)
#   ./ubongo-ctl.sh stop      # TERM, wait up to 10s, KILL fallback
#   ./ubongo-ctl.sh restart
#   ./ubongo-ctl.sh status    # exit 0 when running, 1 when not
#
# For reboot-survival on the Pi/Ubuntu box, prefer the systemd unit in
# deploy/ubongo-web.service; this script is the everywhere-else alternative.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

PIDFILE="data/ubongo-web.pid"
LOGFILE="data/ubongo-web.log"

pid() { cat "$PIDFILE" 2>/dev/null || true; }

is_running() {
  local p
  p="$(pid)"
  [ -n "$p" ] && kill -0 "$p" 2>/dev/null
}

start() {
  if is_running; then
    echo "Already running (pid $(pid))."
    return 0
  fi
  rm -f "$PIDFILE"
  mkdir -p data
  # start-ubongo-web.sh execs streamlit, so $! is the server's pid.
  nohup ./start-ubongo-web.sh >> "$LOGFILE" 2>&1 &
  echo $! > "$PIDFILE"
  sleep 2
  if is_running; then
    echo "Started (pid $(pid)). Log: $LOGFILE"
  else
    echo "Failed to start — last log lines:" >&2
    tail -5 "$LOGFILE" >&2 || true
    rm -f "$PIDFILE"
    return 1
  fi
}

stop() {
  if ! is_running; then
    if [ -f "$PIDFILE" ]; then
      echo "Not running (removing stale pidfile)."
      rm -f "$PIDFILE"
    else
      echo "Not running."
    fi
    return 0
  fi
  local p
  p="$(pid)"
  kill "$p"
  for _ in $(seq 1 10); do
    if ! kill -0 "$p" 2>/dev/null; then
      rm -f "$PIDFILE"
      echo "Stopped."
      return 0
    fi
    sleep 1
  done
  echo "Did not stop in 10s; sending KILL." >&2
  kill -9 "$p" 2>/dev/null || true
  rm -f "$PIDFILE"
  echo "Stopped (killed)."
}

status() {
  if is_running; then
    echo "Running (pid $(pid)). Log: $LOGFILE"
    return 0
  fi
  echo "Not running."
  return 1
}

case "${1:-}" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  status)  status ;;
  *)
    echo "Usage: $0 start|stop|restart|status" >&2
    exit 2
    ;;
esac
