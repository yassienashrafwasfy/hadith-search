#!/usr/bin/env bash
# Dead-code check with vulture. Settings (paths, excludes, ignore lists, min_confidence) live in
# [tool.vulture] in pyproject.toml; this script only runs vulture from the repo root and formats
# the result. It never installs anything and never writes into the repo.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: tools/dead-code.sh [check|report|whitelist] [--min-confidence N]
       tools/dead-code.sh --help

Modes:
  check      (default) print findings grouped by file, exit 1 if there are any, 0 if clean.
  report     always exit 0; write a Markdown report (findings per file and per type) to
             $DEAD_CODE_OUT, else $CLAUDE_JOB_DIR/tmp/dead-code-report.md, else
             ${TMPDIR:-/tmp}/dead-code-report.md. Never inside the repo by default.
  whitelist  print a vulture whitelist candidate to stdout (vulture --make-whitelist).
             Review it first; the script does not write it anywhere.

Options:
  --min-confidence N   override min_confidence from pyproject.toml (0-100)
  -h, --help           show this text

Uses .venv/bin/vulture when present, else vulture on PATH. Exit 2 on a usage or tool error.
USAGE
}

log() { echo "dead-code: $*" >&2; }
die() { log "$*"; exit 2; }

cd "$(dirname "$0")/.."

mode=check
min_conf=
while [ $# -gt 0 ]; do
  case "$1" in
    check | report | whitelist) mode=$1 ;;
    --min-confidence)
      [ $# -ge 2 ] || die "--min-confidence needs a number"
      min_conf=$2
      shift
      ;;
    --min-confidence=*) min_conf=${1#*=} ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
  shift
done
if [ -n "$min_conf" ]; then
  case "$min_conf" in
    '' | *[!0-9]*) die "--min-confidence must be a whole number from 0 to 100" ;;
  esac
  [ "$min_conf" -le 100 ] || die "--min-confidence must be a whole number from 0 to 100"
fi

if [ -x .venv/bin/vulture ]; then
  VULTURE=.venv/bin/vulture
elif command -v vulture >/dev/null 2>&1; then
  VULTURE=vulture
else
  die "vulture not found. Install the dev tools yourself: uv pip install --python .venv/bin/python -r requirements-dev.txt (or pip install -r requirements-dev.txt)"
fi

vargs=()
[ -z "$min_conf" ] || vargs+=(--min-confidence "$min_conf")

# vulture exits 0 when clean, 3 when it found dead code, anything else is an error.
run_vulture() { # prints output to $raw, returns vulture's exit code
  local rc=0
  "$VULTURE" "${vargs[@]}" "$@" >"$raw" 2>&1 || rc=$?
  return "$rc"
}

# "path:12: unused function 'x' (60% confidence)" -> "path<TAB>12<TAB>type<TAB>message<TAB>60"
parse() {
  sed -nE 's/^(.+):([0-9]+): (.*) \(([0-9]+)% confidence(, [0-9]+ lines?)?\)$/\1\t\2\t\3\t\4/p' "$raw" |
    awk -F'\t' -v OFS='\t' '{
      split($3, w, " ")
      if ($3 ~ /^unreachable/) t = "unreachable code"
      else if ($3 ~ /^unsatisfiable/) t = "unsatisfiable condition"
      else t = w[1] " " w[2]
      print $1, $2, t, $3, $4 }' |
    sort -t "$(printf '\t')" -k1,1 -k2,2n
}

raw=$(mktemp)
trap 'rm -f "$raw"' EXIT

if [ "$mode" = whitelist ]; then
  rc=0
  run_vulture --make-whitelist || rc=$?
  case "$rc" in 0 | 3) cat "$raw" ;; *) cat "$raw" >&2; die "vulture failed (exit $rc)" ;; esac
  exit 0
fi

rc=0
run_vulture || rc=$?
case "$rc" in 0 | 3) ;; *) cat "$raw" >&2; die "vulture failed (exit $rc)" ;; esac
rows=$(parse)
count=0
[ -z "$rows" ] || count=$(printf '%s\n' "$rows" | wc -l)
if [ "$rc" = 3 ] && [ "$count" = 0 ]; then
  cat "$raw" >&2
  die "vulture reported dead code in a format this script cannot read"
fi
files=0
[ "$count" = 0 ] || files=$(printf '%s\n' "$rows" | cut -f1 | sort -u | wc -l)
summary="$count finding(s) in $files file(s)"

if [ "$mode" = check ]; then
  if [ "$count" = 0 ]; then
    echo "dead-code: clean, no findings"
    exit 0
  fi
  printf '%s\n' "$rows" | awk -F'\t' '
    $1 != prev { if (prev != "") print ""; print $1; prev = $1 }
    { printf "  line %s: %s (%s%%)\n", $2, $4, $5 }'
  echo
  echo "dead-code: $summary"
  exit 1
fi

# report mode
if [ -n "${DEAD_CODE_OUT:-}" ]; then
  out=$DEAD_CODE_OUT
elif [ -n "${CLAUDE_JOB_DIR:-}" ]; then
  out=$CLAUDE_JOB_DIR/tmp/dead-code-report.md
else
  out=${TMPDIR:-/tmp}/dead-code-report.md
fi
mkdir -p "$(dirname "$out")"
{
  echo "# Dead code report"
  echo
  echo "Generated $(date -u +%Y-%m-%dT%H:%M:%SZ) with $("$VULTURE" --version 2>&1 | head -n1)${min_conf:+, min confidence $min_conf}."
  echo
  echo "$summary."
  if [ "$count" != 0 ]; then
    echo
    echo "## By file"
    printf '%s\n' "$rows" | awk -F'\t' '
      $1 != prev { print ""; print "### `" $1 "`"; print ""; prev = $1 }
      { printf "- line %s: %s (%s%%)\n", $2, $4, $5 }'
    echo
    echo "## By type"
    printf '%s\n' "$rows" | sort -t "$(printf '\t')" -k3,3 -s | awk -F'\t' '
      $3 != prev { print ""; print "### " $3; print ""; prev = $3 }
      { printf "- `%s:%s` %s (%s%%)\n", $1, $2, $4, $5 }'
  fi
} >"$out"
echo "dead-code: $summary; report written to $out"
exit 0
