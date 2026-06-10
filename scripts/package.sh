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

# Bundle name tracks the project version (single source of truth: the root
# VERSION file), so the artifact self-identifies (e.g. ubongo-v0.1.2). pyproject
# is cross-checked so the two never drift silently.
VERSION="$(tr -d '[:space:]' < VERSION)"
PYPROJECT_VERSION="$(grep -m1 '^version = ' pyproject.toml | cut -d'"' -f2)"
if [ "$VERSION" != "$PYPROJECT_VERSION" ]; then
  echo "WARNING: VERSION ($VERSION) != pyproject.toml version ($PYPROJECT_VERSION); keep them in sync." >&2
fi
NAME="ubongo-v${VERSION}"
OUT="dist/$NAME"

rm -rf "$OUT" "dist/$NAME.zip"
mkdir -p "$OUT"

echo "==> Assembling $OUT"

# Runtime code + config + docs + tests (for the smoke playbook).
cp -R src config docs tests "$OUT/"

# Top-level files needed to install / run / read.
cp pyproject.toml uv.lock VERSION CHANGELOG.md \
   README.md CONTEXT.md STATE.md STATUS.md UBONGO_BUILD.md UBONGO_VISION.md \
   install.sh start-ubongo.sh start-ubongo-web.sh .env.example \
   "$OUT/"

# Empty runtime dirs (created clean; no user data shipped).
mkdir -p "$OUT/data" "$OUT/vault/daily" "$OUT/vault/system"
: > "$OUT/vault/.gitkeep"

# Strip caches / local artifacts.
find "$OUT" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$OUT" -name '*.py[co]' -delete 2>/dev/null || true
find "$OUT" -name '.DS_Store' -delete 2>/dev/null || true
rm -rf "$OUT/.pytest_cache" "$OUT"/**/.pytest_cache 2>/dev/null || true

chmod +x "$OUT/install.sh" "$OUT/start-ubongo.sh" "$OUT/start-ubongo-web.sh"

echo "==> Zipping"
( cd dist && zip -rq "$NAME.zip" "$NAME" )

# The bootstrap installer ships NEXT TO the zip (it opens the zip). Distribution
# is exactly these two files: install-ubongo.sh + the zip.
cp install-ubongo.sh dist/
chmod +x dist/install-ubongo.sh

echo
echo "Bootstrap     : dist/install-ubongo.sh   (run this on the target)"
echo "Bundle zip    : dist/$NAME.zip  ($(du -h "dist/$NAME.zip" | cut -f1))"
echo "Bundle folder : $OUT"
echo "Files         : $(find "$OUT" -type f | wc -l | tr -d ' ')"
echo
echo "To deploy: copy dist/install-ubongo.sh + dist/$NAME.zip to the target (macOS or"
echo "Linux), then run  ./install-ubongo.sh"
