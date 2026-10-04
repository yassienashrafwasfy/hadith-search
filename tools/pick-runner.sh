#!/usr/bin/env bash
# Choose the runner for the CI jobs: the self-hosted one (label hadith-ci) when it is online and
# idle and the event is trusted, otherwise GitHub-hosted (ubuntu-latest). It never fails the
# workflow: every problem ends in the hosted fallback, and the reason goes to the job summary.
#
# Inputs (environment):
#   EVENT_NAME            github.event_name
#   HEAD_REPO             full name of the pull request's head repository (pull_request only)
#   REPOSITORY            github.repository
#   RUNNER_STATUS_TOKEN   token that may read the repository's runners (secret, may be empty)
#   RUNNER_LABEL          label to look for (default hadith-ci)
#   API_URL               default https://api.github.com (the tests point it at a local server)
#   GITHUB_OUTPUT         file that gets runner=<json list> and self_hosted=true|false
#   GITHUB_STEP_SUMMARY   file that gets one line saying what was chosen and why (optional)
#
# Self-hosted is only for trusted events: push, schedule, workflow_dispatch, and pull requests
# from a branch of this same repository. A pull request from a fork always runs on GitHub-hosted
# runners, so code from outside never runs on the owner's machine.
#
# Race: the check and the job start are two steps. If another job takes the runner in between,
# the job waits in the queue for it (ephemeral runners come back within seconds), it does not
# fall back by itself.
set -euo pipefail

label=${RUNNER_LABEL:-hadith-ci}
api=${API_URL:-https://api.github.com}
out=${GITHUB_OUTPUT:-/dev/stdout}
summary=${GITHUB_STEP_SUMMARY:-/dev/null}

hosted='["ubuntu-latest"]'
self='["self-hosted","'"$label"'"]'

choose() { # choose <json> <true|false> <reason>
  { echo "runner=$1"; echo "self_hosted=$2"; } >>"$out"
  echo "pick-runner: $1 ($3)" >&2
  echo "Runner: \`$1\`. $3." >>"$summary"
  exit 0
}

case "${EVENT_NAME:-}" in
  push | schedule | workflow_dispatch) ;;
  pull_request)
    if [ -z "${HEAD_REPO:-}" ] || [ "$HEAD_REPO" != "${REPOSITORY:-}" ]; then
      choose "$hosted" false "Pull request from a fork, so GitHub-hosted only"
    fi
    ;;
  *) choose "$hosted" false "Event '${EVENT_NAME:-}' is not trusted for the self-hosted runner" ;;
esac

[ -n "${RUNNER_STATUS_TOKEN:-}" ] || choose "$hosted" false "RUNNER_STATUS_TOKEN is not set"
[ -n "${REPOSITORY:-}" ] || choose "$hosted" false "REPOSITORY is not set"

body=$(mktemp)
trap 'rm -f "$body"' EXIT
code=$(curl -sS -m 20 -o "$body" -w '%{http_code}' \
  -H "Authorization: Bearer $RUNNER_STATUS_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "$api/repos/$REPOSITORY/actions/runners?per_page=100" 2>/dev/null) || code=000
[ "$code" = 200 ] || choose "$hosted" false "The runners API answered HTTP $code"

# online + idle -> use it; online + busy or offline -> fall back, and say which
state=$(python3 - "$body" "$label" <<'PY' 2>/dev/null || echo bad
import json, sys
try:
    runners = json.load(open(sys.argv[1])).get("runners", [])
except Exception:
    print("bad")
    sys.exit()
mine = [r for r in runners if any(l.get("name") == sys.argv[2] for l in r.get("labels", []))]
if not mine:
    print("none")
elif any(r.get("status") == "online" and not r.get("busy") for r in mine):
    print("idle")
elif any(r.get("status") == "online" for r in mine):
    print("busy")
else:
    print("offline")
PY
)

case "$state" in
  idle) choose "$self" true "Self-hosted runner '$label' is online and idle" ;;
  busy) choose "$hosted" false "Self-hosted runner '$label' is online but busy" ;;
  offline) choose "$hosted" false "Self-hosted runner '$label' is offline" ;;
  none) choose "$hosted" false "No runner with the label '$label' is registered" ;;
  *) choose "$hosted" false "The runners API answer could not be read" ;;
esac
