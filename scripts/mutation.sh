#!/usr/bin/env bash
#
# Run mutation testing (mutmut 3.x) over the scoped source paths configured in
# pyproject.toml's [tool.mutmut]. mutmut is coverage-guided: it runs the test
# suite once to map coverage, then runs only the covering tests per mutant.
#
#   ./scripts/mutation.sh           # run all configured mutants, then show results
#   ./scripts/mutation.sh results   # just print the results of the last run
#   ./scripts/mutation.sh stats     # export mutants/mutmut-cicd-stats.json
#
# See docs/mutation-testing.md for how to read the output and the scoping policy.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-.venv/bin/python}"

case "${1:-run}" in
  run)
    echo "==> mutmut run (scoped via pyproject [tool.mutmut])"
    "$PY" -m mutmut run || true   # non-zero exit when survivors remain; we show them next
    echo
    echo "==> mutmut results"
    "$PY" -m mutmut results
    ;;
  results)
    "$PY" -m mutmut results
    ;;
  stats)
    "$PY" -m mutmut export_cicd_stats
    ;;
  *)
    echo "usage: $0 [run|results|stats]" >&2
    exit 2
    ;;
esac
