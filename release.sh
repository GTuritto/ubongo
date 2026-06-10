#!/usr/bin/env bash
#
# Create a new Ubongo distribution version: bump the version per the
# v0.MAJOR.PHASE scheme, write the CHANGELOG entry, and build the bundle.
#
# Versioning (see CHANGELOG.md): MAJOR bumps when a whole build plan completes;
# otherwise each completed phase increments PHASE (the third number).
#
#   ./release.sh phase "Title"  ["longer body..."]   # 0.1.2 -> 0.1.3
#   ./release.sh major "Title"  ["longer body..."]   # 0.1.x -> 0.2.0
#
# Edits the working tree (VERSION, pyproject.toml, CHANGELOG.md) and builds
# dist/. It does NOT commit — review, then commit (it prints the command).
# Cross-platform (macOS + Linux): all file edits go through python3, not sed -i.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

KIND="${1:-}"
TITLE="${2:-}"
BODY=""
if [ "$#" -gt 2 ]; then shift 2; BODY="$*"; fi

case "$KIND" in
  phase|major) ;;
  *) echo "Usage: ./release.sh <phase|major> \"<title>\" [\"body...\"]" >&2; exit 1 ;;
esac
[ -n "$TITLE" ] || { echo "Give a short title for the changelog entry." >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required." >&2; exit 1; }

CUR="$(tr -d '[:space:]' < VERSION)"
NEW="$(python3 - "$CUR" "$KIND" <<'PY'
import sys
cur, kind = sys.argv[1], sys.argv[2]
parts = (cur.split(".") + ["0", "0", "0"])[:3]
a, b, c = (int(p) for p in parts)
if kind == "phase":
    c += 1
else:               # major: a build plan completed
    b += 1; c = 0
print(f"{a}.{b}.{c}")
PY
)"

echo "==> Releasing v${CUR} -> v${NEW}  (${KIND})"

# 1. VERSION (single source of truth)
printf '%s\n' "$NEW" > VERSION

# 2. pyproject.toml
python3 - "$NEW" <<'PY'
import re, sys, pathlib
new = sys.argv[1]
p = pathlib.Path("pyproject.toml")
t = p.read_text()
t, n = re.subn(r'^version = "[^"]*"', f'version = "{new}"', t, count=1, flags=re.M)
if n != 1:
    sys.exit("could not find 'version = ...' in pyproject.toml")
p.write_text(t)
PY

# 3. CHANGELOG.md — prepend the entry above the newest existing one
python3 - "$NEW" "$TITLE" "$BODY" <<'PY'
import sys, re, pathlib, datetime
new, title, body = sys.argv[1], sys.argv[2], sys.argv[3]
date = datetime.date.today().isoformat()
entry = f"## v{new} — {title}\n\nDate: {date}\n\n{body or title}\n\n"
p = pathlib.Path("CHANGELOG.md")
t = p.read_text()
m = re.search(r'^## v', t, flags=re.M)
t = (t[:m.start()] + entry + t[m.start():]) if m else (t.rstrip() + "\n\n" + entry)
p.write_text(t)
PY

echo "==> Building distribution for v${NEW}"
./scripts/package.sh

echo
echo "Released v${NEW}. Review CHANGELOG.md, then commit + tag:"
echo "  git add VERSION pyproject.toml CHANGELOG.md"
echo "  git commit -m \"release: v${NEW} — ${TITLE}\""
echo "  git tag v${NEW}    # optional"
