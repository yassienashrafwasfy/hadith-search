#!/usr/bin/env bash
# Self-test for tools/dead-code.sh. Builds a throwaway project in a temp directory (one dead
# function, one used function), runs a copy of the script there and checks exit codes, the report
# and that whitelist mode prints without writing. The real repo is not touched.
set -euo pipefail
cd "$(dirname "$0")/.."

REPO=$PWD
if [ -x "$REPO/.venv/bin/vulture" ]; then
  REAL_VULTURE=$REPO/.venv/bin/vulture
else
  REAL_VULTURE=$(command -v vulture) || { echo "test-dead-code: vulture not found" >&2; exit 2; }
fi
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
fails=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fails=$((fails + 1)); }
check() { # description, command...
  local d=$1; shift
  if "$@"; then ok "$d"; else bad "$d"; fi
}

mkdir -p "$T/proj/tools" "$T/proj/src" "$T/proj/.venv/bin" "$T/out"
cp tools/dead-code.sh "$T/proj/tools/"
ln -s "$REAL_VULTURE" "$T/proj/.venv/bin/vulture"
cat >"$T/proj/pyproject.toml" <<'TOML'
[tool.vulture]
paths = ["src"]
min_confidence = 60
TOML
cat >"$T/proj/src/clean.py" <<'PY'
def used():
    return 1


print(used())
PY
S=$T/proj/tools/dead-code.sh
export DEAD_CODE_OUT=$T/out/report.md

# clean project
rc=0; out=$("$S" check 2>&1) || rc=$?
check "clean project exits 0" [ "$rc" = 0 ]
check "clean summary line" grep -q 'clean' <<<"$out"

# dead function; run from another directory to prove it finds the root itself
cat >"$T/proj/src/dead.py" <<'PY'
def never_called():
    return 2
PY
rc=0; out=$(cd / && "$S" check 2>&1) || rc=$?
check "dead function exits 1" [ "$rc" = 1 ]
check "finding names file and function" grep -q "never_called" <<<"$out"
check "finding is grouped under the file" grep -q '^src/dead.py$' <<<"$out"
check "summary counts the finding" grep -q '1 finding(s) in 1 file(s)' <<<"$out"
check "clean file is not listed" bash -c '! grep -q clean.py <<<"$1"' _ "$out"

# min confidence above the finding's 60% hides it
rc=0; "$S" check --min-confidence 70 >/dev/null 2>&1 || rc=$?
check "--min-confidence 70 hides a 60% finding" [ "$rc" = 0 ]
rc=0; "$S" check --min-confidence abc >/dev/null 2>&1 || rc=$?
check "bad --min-confidence exits 2" [ "$rc" = 2 ]

# report mode
rc=0; "$S" report >/dev/null 2>&1 || rc=$?
check "report exits 0 even with findings" [ "$rc" = 0 ]
check "report file written to DEAD_CODE_OUT" [ -s "$DEAD_CODE_OUT" ]
check "report lists the file" grep -q 'src/dead.py' "$DEAD_CODE_OUT"
check "report has a by-file section" grep -q '^## By file' "$DEAD_CODE_OUT"
check "report has a by-type section with the type" grep -q '^### unused function' "$DEAD_CODE_OUT"
check "report is not inside the project" bash -c '[ -z "$(find "$1" -name "*.md")" ]' _ "$T/proj"

# whitelist mode prints and writes nothing
before=$(cd "$T/proj" && find . -type f -not -path './.venv/*' | sort | xargs sha1sum)
rc=0; out=$("$S" whitelist 2>/dev/null) || rc=$?
after=$(cd "$T/proj" && find . -type f -not -path './.venv/*' | sort | xargs sha1sum)
check "whitelist exits 0" [ "$rc" = 0 ]
check "whitelist prints the dead name" grep -q 'never_called' <<<"$out"
check "whitelist writes no file" [ "$before" = "$after" ]

# default report location without DEAD_CODE_OUT, CLAUDE_JOB_DIR set
unset DEAD_CODE_OUT
CLAUDE_JOB_DIR=$T/job "$S" report >/dev/null 2>&1 || true
check "report defaults to \$CLAUDE_JOB_DIR/tmp" [ -s "$T/job/tmp/dead-code-report.md" ]

# missing vulture gives a clear error (only when none is on the minimal PATH)
rm "$T/proj/.venv/bin/vulture"
if PATH=/usr/bin:/bin command -v vulture >/dev/null 2>&1; then
  echo "skip missing-vulture test (vulture is in /usr/bin)"
else
  rc=0; out=$(PATH=/usr/bin:/bin "$S" check 2>&1) || rc=$?
  check "missing vulture exits 2" [ "$rc" = 2 ]
  check "missing vulture points at requirements-dev.txt" grep -q requirements-dev.txt <<<"$out"
fi

if [ "$fails" = 0 ]; then echo "test-dead-code: all passed"; else echo "test-dead-code: $fails failed"; exit 1; fi
