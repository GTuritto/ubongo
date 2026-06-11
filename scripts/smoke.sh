#!/usr/bin/env bash
#
# Ubongo — automated smoke gate. The scriptable subset of the manual playbook
# (tests/manual/smoke_test.md), runnable locally and in CI. Two layers:
#
#   ./scripts/smoke.sh           # deterministic layer only — no LLM calls,
#                                #   works with a dummy OPENROUTER_API_KEY
#   ./scripts/smoke.sh --live    # + a small live subset (3 real model calls):
#                                #   persona one-shot, governance gate rc,
#                                #   profiled turn. Needs a real key.
#
# The deterministic layer covers: cold start + log structure + key hygiene,
# missing-key error, context assembly, every command surface, the sandbox
# refusal matrix, the profiler family (including a real tracemalloc report),
# the startup profiler switch (flag, env, override), /exec isolation, and the
# web service controller when streamlit is installed.
#
# The full playbook (live modes, evolution, authoring, vault sync, Obsidian,
# systemd) remains the manual certification; this script is the regression
# gate the pipeline runs before publishing a release.
#
# Override the entrypoint with UBONGO_CMD (default: uv run python -m ubongo).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CMD="${UBONGO_CMD:-uv run python -m ubongo}"
PY="${UBONGO_PY:-uv run python}"
TMP="$(mktemp -d)"
LIVE=0
[ "${1:-}" = "--live" ] && LIVE=1

PASS=0; FAIL=0; SKIP=0
ok()   { echo "PASS  $1"; PASS=$((PASS+1)); }
bad()  { echo "FAIL  $1"; FAIL=$((FAIL+1)); }
skip() { echo "SKIP  $1"; SKIP=$((SKIP+1)); }
expect() { # <name> <pattern> <file>
  if grep -q "$2" "$3"; then ok "$1"; else bad "$1  (missing: $2)"; fi
}
expect_rc() { # <name> <want> <got>
  if [ "$3" -eq "$2" ]; then ok "$1 (rc=$3)"; else bad "$1 (rc=$3, want $2)"; fi
}

echo "== Ubongo smoke gate (deterministic layer$( [ $LIVE -eq 1 ] && echo ' + live subset')) =="
rm -f data/ubongo.db

# ---------- cold start / logging / key hygiene ----------
printf '/exit\n' | $CMD >"$TMP/cold.out" 2>"$TMP/cold.err"; rc=$?
expect_rc "cold start exits clean" 0 $rc
expect "startup event logged" '"event": "startup"' "$TMP/cold.err"
KEY="${OPENROUTER_API_KEY:-$(grep -m1 '^OPENROUTER_API_KEY=' .env 2>/dev/null | cut -d= -f2-)}"
if [ -n "$KEY" ] && grep -qF "$KEY" "$TMP/cold.err" "$TMP/cold.out" 2>/dev/null; then
  bad "API key leaked into output"
elif [ -n "$KEY" ]; then
  ok "API key not leaked"
else
  skip "API key leak check (no key in env or .env)"
fi

# missing key: hide .env if present, strip the env var
moved=0; [ -f .env ] && mv .env "$TMP/.env.bak" && moved=1
env -u OPENROUTER_API_KEY $CMD >"$TMP/nokey.out" 2>"$TMP/nokey.err"; rc=$?
[ $moved -eq 1 ] && mv "$TMP/.env.bak" .env
expect_rc "missing key exits 1" 1 $rc
expect "missing key message" "OPENROUTER_API_KEY not set" "$TMP/nokey.err"

# context assembly
$PY -c "from ubongo.context import build_system_prompt; p=build_system_prompt('architect'); assert p.startswith('# UBONGO.md')" \
  && ok "context assembly (UBONGO.md first)" || bad "context assembly"

# ---------- command surfaces + sandbox + profiler (one piped session) ----------
printf '%s\n' '/skills' '/agents' '/policy' '/mode list' '/optimize' '/evaluate' \
  '/queue abc' '/exec echo smoke ok' '/exec rm -rf /' '/exec ls; cat /etc/passwd' \
  '/exec cat ../../etc/passwd' '/exec curl https://example.com' \
  '/skill phantom' '/mode phantom' '/profile' '/profile bogus' \
  '/profile cpu status' '/profile mem on' '/profile mem' '/profile mem off' \
  '/evolution status' '/authoring status' '/audit' '/conflicts' '/foo' '/exit' \
  | UBONGO_PROFILE=all $CMD >"$TMP/surf.out" 2>/dev/null; rc=$?
expect_rc "surface session exits clean" 0 $rc
expect "startup switch armed notice"    "Profiling armed at startup: cpu"        "$TMP/surf.out"
expect "/skills lists the shipped skill" "summarize-conversation"                "$TMP/surf.out"
expect "/agents table"                  "Registered agents:"                     "$TMP/surf.out"
expect "/policy prints the matrix"      "require_approval"                       "$TMP/surf.out"
expect "/mode list shows workflows"     "mode="                                  "$TMP/surf.out"
expect "/optimize lists targets"        "routing:default"                        "$TMP/surf.out"
expect "/evaluate empty-db message"     "No evaluable targets"                   "$TMP/surf.out"
expect "/queue usage on bad arg"        "Usage: /queue \[N\]"                    "$TMP/surf.out"
expect "/exec happy path"               "smoke ok"                               "$TMP/surf.out"
expect "sandbox: allowlist refusal"     "program 'rm' not in allowlist"          "$TMP/surf.out"
expect "sandbox: metacharacter refusal" "shell metacharacter ';' rejected"       "$TMP/surf.out"
expect "sandbox: traversal refusal"     "path fragment '..' rejected"            "$TMP/surf.out"
expect "sandbox: network refusal"       "program 'curl' not in allowlist"        "$TMP/surf.out"
expect "unknown skill rejected"         "Unknown skill: phantom"                 "$TMP/surf.out"
expect "unknown workflow rejected"      "Unknown workflow: phantom"              "$TMP/surf.out"
expect "/profile empty-db message"      "No runs recorded yet"                   "$TMP/surf.out"
expect "/profile usage on bad arg"      "Usage: /profile"                        "$TMP/surf.out"
expect "/profile cpu armed from env"    "CPU profiling is on"                    "$TMP/surf.out"
expect "tracemalloc growth report"      "Memory growth since baseline"           "$TMP/surf.out"
expect "memory disarm message"          "Memory profiling off"                   "$TMP/surf.out"
expect "evolution loop boots paused"    "status=paused"                          "$TMP/surf.out"
expect "authoring daemon paused"        "Authoring daemon: paused"               "$TMP/surf.out"
expect "help banner includes /profile"  "/profile \[agents|models|modes|cpu|mem\] \[N\]" "$TMP/surf.out"

# /exec must not create a workflow_run
N=$($PY - <<'EOF'
import sqlite3
print(sqlite3.connect("data/ubongo.db").execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0])
EOF
)
[ "$N" = "0" ] && ok "/exec creates no workflow_run" || bad "/exec created workflow_runs ($N)"

# ---------- startup switch precedence ----------
printf '/profile cpu status\n/exit\n' | $CMD --profile mem >"$TMP/sw1.out" 2>/dev/null
expect "flag --profile mem arms mem only" "Profiling armed at startup: mem" "$TMP/sw1.out"
printf '/profile cpu status\n/exit\n' | UBONGO_PROFILE=cpu $CMD --profile off >"$TMP/sw2.out" 2>/dev/null
grep -q "Profiling armed" "$TMP/sw2.out" \
  && bad "--profile off should override env" || ok "--profile off overrides UBONGO_PROFILE"
printf '/exit\n' | UBONGO_PROFILE=bogus $CMD >"$TMP/sw3.out" 2>"$TMP/sw3.err"; rc=$?
expect_rc "invalid UBONGO_PROFILE never blocks startup" 0 $rc

# ---------- web service controller (needs streamlit) ----------
if $PY -c "import streamlit" >/dev/null 2>&1; then
  ./ubongo-ctl.sh start >"$TMP/ctl.out" 2>&1 && sleep 4 \
    && ./ubongo-ctl.sh status >>"$TMP/ctl.out" 2>&1; rc=$?
  expect_rc "ctl start + status" 0 $rc
  if command -v curl >/dev/null 2>&1; then
    code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${UBONGO_WEB_PORT:-8501}" || echo 000)
    [ "$code" = "200" ] && ok "web service answers HTTP 200" || bad "web service HTTP $code"
  fi
  ./ubongo-ctl.sh stop >>"$TMP/ctl.out" 2>&1
  ./ubongo-ctl.sh status >/dev/null 2>&1 && bad "ctl stop left service running" || ok "ctl stop + status rc=1 when down"
else
  skip "web service controller (streamlit not installed; install with --extra web)"
fi

# ---------- live subset (real model; ~3 calls) ----------
if [ $LIVE -eq 1 ]; then
  $CMD send "Reply with exactly: smoke live ok" --persona casual >"$TMP/live1.out" 2>/dev/null; rc=$?
  expect_rc "live one-shot turn" 0 $rc
  [ -s "$TMP/live1.out" ] && ok "live response non-empty" || bad "live response empty"
  $CMD send "delete the entire vault" --persona casual >"$TMP/live2.out" 2>&1; rc=$?
  expect_rc "governance gate blocks in one-shot" 1 $rc
  expect "governance gated message" "approval" "$TMP/live2.out"
  # message-first: a bare --profile before the positional would eat it as the
  # flag's value (argparse nargs="?")
  $CMD send "Reply with one word." --profile >"$TMP/live3.out" 2>/dev/null; rc=$?
  expect_rc "profiled live turn" 0 $rc
  expect "cpu profile artifact written" "CPU profile written to" "$TMP/live3.out"
fi

# ---------- cleanup + verdict ----------
rm -f data/ubongo.db
rm -rf "$TMP"
echo
echo "== smoke gate: $PASS passed, $FAIL failed, $SKIP skipped =="
[ $FAIL -eq 0 ] || exit 1
