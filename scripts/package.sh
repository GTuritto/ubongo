#!/usr/bin/env bash
#
# Build a self-contained Ubongo deployment bundle (a folder + a .zip) suitable
# for copying onto a Raspberry Pi 5 / Ubuntu box. Excludes git/venv/caches and
# all local data. Output lands in ./dist (gitignored).
#
#   ./scripts/package.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

NAME="ubongo-v0.1"
OUT="dist/$NAME"

rm -rf "$OUT" "dist/$NAME.zip"
mkdir -p "$OUT"

echo "==> Assembling $OUT"

# Runtime code + config + docs + tests (for the smoke playbook).
cp -R src config docs tests "$OUT/"

# Top-level files needed to install / run / read.
cp pyproject.toml uv.lock \
   README.md CONTEXT.md STATUS.md UBONGO_BUILD.md UBONGO_VISION.md \
   install.sh start-ubongo.sh .env.example \
   "$OUT/"

# Empty runtime dirs (created clean; no user data shipped).
mkdir -p "$OUT/data" "$OUT/vault/daily" "$OUT/vault/system"
: > "$OUT/vault/.gitkeep"

# Strip caches / local artifacts.
find "$OUT" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$OUT" -name '*.py[co]' -delete 2>/dev/null || true
find "$OUT" -name '.DS_Store' -delete 2>/dev/null || true
rm -rf "$OUT/.pytest_cache" "$OUT"/**/.pytest_cache 2>/dev/null || true

chmod +x "$OUT/install.sh" "$OUT/start-ubongo.sh"

echo "==> Zipping"
( cd dist && zip -rq "$NAME.zip" "$NAME" )

echo
echo "Bundle folder : $OUT"
echo "Bundle zip    : dist/$NAME.zip  ($(du -h "dist/$NAME.zip" | cut -f1))"
echo "Files         : $(find "$OUT" -type f | wc -l | tr -d ' ')"
