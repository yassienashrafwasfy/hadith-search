#!/usr/bin/env bash
# Mutation testing with mutmut on the pure-logic modules listed in tools/mutation.pyproject.toml.
#   tools/mutation.sh            # run all mutants
#   tools/mutation.sh results    # show surviving mutants
#   tools/mutation.sh show <name>
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
stage="$root/.mutation"
mkdir -p "$stage"
rsync -a --delete --exclude mutants --exclude __pycache__ --exclude data --exclude .pytest_cache \
  "$root/backend/" "$stage/"
rsync -a --delete --exclude __pycache__ "$root/tests/" "$stage/tests/"
cp "$root/tools/mutation.pyproject.toml" "$stage/pyproject.toml"
cd "$stage"
"$root/.venv/bin/mutmut" "${@:-run}"
