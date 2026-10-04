#!/usr/bin/env bash
# Pick the best model in MLflow, export it to ONNX, re-embed the corpus and (optionally) release it.
#
#   tools/promote_model.sh run --registered-model NAME [--margin 0.01] [--dry-run]
#                                  select, export, parity check, re-embed into a NEW table, stage.
#                                  Production does not change.
#   tools/promote_model.sh run --registered-model NAME --promote
#                                  the same, then tools/deploy.sh deploy <live image> + promote
#                                  (deploy.sh switches back by itself if the new colour fails)
#   tools/promote_model.sh prune [--keep 3] [--dry-run]
#                                  drop old releases' embedding tables and model folders
#
# Needs MLFLOW_TRACKING_URI (and MLFLOW_TRACKING_TOKEN if the server asks for one) and
# DATABASE_URL in the environment; install the extra package with
#   uv pip install -r requirements.txt -r requirements-mlops.txt
# See docs/HANDOFF.md item 40. Exit codes: 0 done, 1 failed, 2 bad usage or settings,
# 3 no version beats the live model, 4 the ONNX export did not match the original.
set -euo pipefail
cd "$(dirname "$0")/.."

STATE_DIR=deploy/state
PY=.venv/bin/python
[ -x "$PY" ] || { echo "error: $PY not found; create the venv first (see CLAUDE.md)" >&2; exit 2; }

promote=0 dry=0 args=()
for a in "$@"; do
  case "$a" in
    --promote) promote=1 ;;
    --dry-run) dry=1; args+=("$a") ;;
    *) args+=("$a") ;;
  esac
done
[ "${#args[@]}" -gt 0 ] || { sed -n '2,15p' "$0"; exit 2; }
[ "${args[0]}" = run ] || [ "${args[0]}" = prune ] || { sed -n '2,15p' "$0"; exit 2; }
[ "$promote" -eq 0 ] || [ "${args[0]}" = run ] || { echo "error: --promote only goes with run" >&2; exit 2; }

(cd backend && "../$PY" -m scripts.promote_model "${args[@]}")
[ "${args[0]}" = run ] && [ "$promote" -eq 1 ] || exit 0

staged=$STATE_DIR/model.staged.env
if [ "$dry" -eq 1 ]; then
  echo "promote_model: dry run: would copy $staged to $STATE_DIR/model.env, run deploy.sh deploy <live image> and promote"
  exit 0
fi
[ -f "$staged" ] || { echo "error: nothing staged ($staged is missing)" >&2; exit 1; }

active=$(sed -n 's/^ACTIVE=//p' "$STATE_DIR/state.env")
key=$(echo "$active" | tr '[:lower:]' '[:upper:]')
image=$(sed -n "s/^${key}_REL_IMAGE=//p" "$STATE_DIR/releases.env" | tail -1)
[ -n "$image" ] || { echo "error: cannot find the live image in $STATE_DIR/releases.env" >&2; exit 1; }

# Keep the old settings so a failed release can put them back; the live colour never reads this file.
if [ -f "$STATE_DIR/model.env" ]; then cp "$STATE_DIR/model.env" "$STATE_DIR/model.env.prev"; else : >"$STATE_DIR/model.env.prev"; fi
cp "$staged" "$STATE_DIR/model.env"
if tools/deploy.sh deploy "$image" && tools/deploy.sh promote; then
  echo "promote_model: promoted. Roll back with: tools/deploy.sh rollback (the database keeps both embedding sets)"
else
  cp "$STATE_DIR/model.env.prev" "$STATE_DIR/model.env"
  echo "error: deploy or promote failed; deploy.sh kept (or restored) the old colour, model.env put back" >&2
  exit 1
fi
