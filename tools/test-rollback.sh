#!/usr/bin/env bash
# Runs tools/deploy.sh (promote, rollback, dry run, automatic rollback) against throwaway stub
# apps and a real nginx on a private Docker network. A copy of the script runs in a temp
# directory, so deploy/state and the real containers (hadith-*) are never touched. A small
# `docker` shim in front of PATH turns `docker compose ...` calls into operations on the stub
# containers; everything else goes to the real docker. Needs Docker; builds no image.
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE=nginxinc/nginx-unprivileged:stable-alpine
NET=rollback-test-$$
T=$(mktemp -d)
REAL_DOCKER=$(command -v docker)
export REAL_DOCKER NET T
trap 'for n in blue green edge; do "$REAL_DOCKER" rm -f "$NET-$n" >/dev/null 2>&1 || true; done; "$REAL_DOCKER" network rm "$NET" >/dev/null 2>&1 || true; rm -rf "$T"' EXIT

mkdir -p "$T/tools" "$T/bin" "$T/deploy/state"
cp tools/deploy.sh "$T/tools/deploy.sh"
chmod 755 "$T" "$T/deploy" "$T/deploy/state"
STATE=$T/deploy/state

# stub app configuration for one colour; MODE is ok, bad-search, busy-search, no-search or bad-health
write_stub() { # colour mode
  local health=200 search=200 links='"searches":{"href":"/api/v1/searches"}'
  [ "$2" != bad-health ] || health=500
  [ "$2" != bad-search ] || search=500
  [ "$2" != busy-search ] || search=503
  if [ "$2" = no-search ]; then search=500; links='"self":{"href":"/api/v1"}'; fi
  cat >"$T/stub-$1.conf" <<CONF
server {
    listen 8000;
    default_type application/json;
    location = /api/v1 { return 200 '{"_links":{$links}}'; }
    location = /api/v1/health { return $health '{"status":"ok"}'; }
    location = /api/v1/searches { return $search '{"results":[]}'; }
    location / { return 404 '{}'; }
}
CONF
  chmod 644 "$T/stub-$1.conf"
}
set_mode() { # colour mode: change a running stub
  write_stub "$1" "$2"
  "$REAL_DOCKER" exec "$NET-$1" nginx -s reload >/dev/null 2>&1 || true
}

# `docker compose` as deploy.sh uses it, mapped onto the stubs
cat >"$T/bin/docker" <<'SHIM'
#!/usr/bin/env bash
if [ "${1:-}" = compose ]; then
  shift
  args="$*"
  args_arr=("$@")
  for i in "${!args_arr[@]}"; do
    if [ "${args_arr[$i]}" = exec ]; then exec "$REAL_DOCKER" exec "$NET-edge" "${args_arr[@]:$((i + 3))}"; fi
  done
  colour=""
  for word in "$@"; do case "$word" in app-blue) colour=blue ;; app-green) colour=green ;; esac; done
  case "$args" in
    *"ps -q"*) "$REAL_DOCKER" ps -q --filter "name=^$NET-$colour\$" ;;
    *" up -d"*) bash "$T/start-stub.sh" "$colour" ;;
    *" stop app-"*) "$REAL_DOCKER" rm -f "$NET-$colour" >/dev/null ;;
  esac
  exit 0
fi
exec "$REAL_DOCKER" "$@"
SHIM
chmod +x "$T/bin/docker"
# the shim cannot call shell functions, so start-stub is a tiny script of its own
cat >"$T/start-stub.sh" <<STUB
#!/usr/bin/env bash
"$REAL_DOCKER" rm -f "$NET-\$1" >/dev/null 2>&1 || true
"$REAL_DOCKER" run -d --name "$NET-\$1" --network "$NET" --network-alias "app-\$1" \
  --health-cmd 'wget -q -O /dev/null http://127.0.0.1:8000/api/v1/health' --health-interval 1s \
  --health-timeout 2s --health-retries 1 \
  -v "$T/stub-\$1.conf:/etc/nginx/conf.d/default.conf:ro" "$IMAGE" >/dev/null
STUB
chmod +x "$T/start-stub.sh"

"$REAL_DOCKER" network create "$NET" >/dev/null
write_stub blue ok
write_stub green ok
"$T/tools/deploy.sh" routing-conf blue 0 >"$STATE/routing.conf"
chmod 644 "$STATE/routing.conf"
"$REAL_DOCKER" run -d --name "$NET-edge" --network "$NET" \
  -v "$PWD/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro" \
  -v "$PWD/nginx/proxy_app.conf:/etc/nginx/proxy_app.conf:ro" \
  -v "$STATE:/etc/nginx/routing:ro" "$IMAGE" >/dev/null

deploy() { # args...: run the copied script with the shim first on PATH; sets OUT and RC
  set +e
  OUT=$(PATH="$T/bin:$PATH" HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-20} CANARY_WATCH=0 PROMOTE_WATCH=${PROMOTE_WATCH:-0} \
    "$T/tools/deploy.sh" "$@" 2>&1)
  RC=$?
  set -e
}
served() { # which colour answers through nginx
  "$REAL_DOCKER" exec "$NET-edge" wget -S -q -O /dev/null http://127.0.0.1:8080/api/v1 2>&1 |
    tr -d '\r' | sed -n 's/.*[Xx]-[Rr]elease: *//p' | tail -1
}
active() { sed -n 's/^ACTIVE=//p' "$STATE/state.env"; }
running() { "$REAL_DOCKER" ps -q --filter "name=^$NET-$1\$" | wc -l | tr -d ' '; }
fingerprint() { cat "$STATE"/state.env "$STATE"/routing.conf "$STATE"/releases.env 2>/dev/null | md5sum; }

# Blue live, green (release 2) deployed and idle. Both stubs healthy.
reset() {
  rm -f "$STATE/releases.env" "$STATE/history.log" "$STATE/schema-step2-applied"
  write_stub blue ok
  write_stub green ok
  printf 'ACTIVE=blue\nCANARY=0\nPREVIOUS=\n' >"$STATE/state.env"
  printf 'BLUE_REL_ID=1\nBLUE_REL_IMAGE=stub/app:1\nLAST=init\n' >"$STATE/releases.env"
  "$T/tools/deploy.sh" routing-conf blue 0 >"$STATE/routing.conf"
  "$REAL_DOCKER" exec "$NET-edge" nginx -s reload >/dev/null 2>&1
  bash "$T/start-stub.sh" blue
  deploy deploy stub/app:2
  [ "$RC" -eq 0 ] || { echo "setup failed: $OUT"; exit 2; }
  sleep 1
}

for _ in $(seq 1 20); do "$REAL_DOCKER" exec "$NET-edge" wget -q -O /dev/null http://127.0.0.1:8080/ 2>/dev/null && break; sleep 0.5; done

fail=0
check() { # description expected actual
  if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: expected '$2', got '$3'"; fail=1; fi
}
contains() { # description needle haystack
  if [[ "$3" == *"$2"* ]]; then echo "ok   $1"; else echo "FAIL $1: no '$2' in: $3"; fail=1; fi
}

echo "--- release is recorded; promote and manual rollback"
reset
check "after deploy: blue still serves" blue "$(served)"
check "deploy recorded release 2 for green" 2 "$(sed -n 's/^GREEN_REL_ID=//p' "$STATE/releases.env")"
deploy promote
check "promote succeeds" 0 "$RC"
check "promote: nginx serves green" green "$(served)"
check "promote: state says green, previous blue" "green blue" "$(active) $(sed -n 's/^PREVIOUS=//p' "$STATE/state.env")"
deploy rollback
check "rollback succeeds" 0 "$RC"
contains "rollback names both releases" "release 2" "$OUT"
check "rollback: nginx serves blue again" blue "$(served)"
check "rollback: green still running" 1 "$(running green)"
deploy rollback
check "second rollback is refused (already rolled back)" 1 "$RC"
check "refused rollback leaves traffic on blue" blue "$(served)"

echo "--- dry run changes nothing"
reset
before=$(fingerprint)
deploy promote --dry-run
check "promote --dry-run exits 0" 0 "$RC"
contains "promote --dry-run says what it would do" "dry run: would" "$OUT"
check "promote --dry-run changed no file" "$before" "$(fingerprint)"
check "promote --dry-run: nginx still serves blue" blue "$(served)"
deploy promote
deploy rollback --dry-run
check "rollback --dry-run exits 0" 0 "$RC"
after_promote=$(fingerprint)
deploy rollback --dry-run
check "rollback --dry-run changed no file" "$after_promote" "$(fingerprint)"
check "rollback --dry-run: nginx still serves green" green "$(served)"

echo "--- rollback is refused when the old colour is down"
reset
deploy promote
"$REAL_DOCKER" rm -f "$NET-blue" >/dev/null
deploy rollback
check "rollback refused" 1 "$RC"
contains "refusal says why" "blue is not running" "$OUT"
check "traffic stays on green" green "$(served)"
check "state unchanged" green "$(active)"
reset
deploy promote
set_mode blue bad-health
sleep 3
deploy rollback
check "rollback refused when old colour is unhealthy" 1 "$RC"
contains "refusal names the health state" "not healthy" "$OUT"
check "traffic stays on green (unhealthy old)" green "$(served)"

echo "--- failed checks"
reset
set_mode green bad-search
deploy promote
check "promote refused when the smoke search fails" 1 "$RC"
contains "refusal names the failed request" "search request" "$OUT"
check "traffic never moved" blue "$(served)"
reset
set_mode green busy-search
SMOKE_RETRY_WAIT=0 deploy promote
check "a busy (503) search is only a warning" 0 "$RC"
contains "the warning says the search was busy" "answered 503 (busy)" "$OUT"
check "traffic moved to green despite the busy search" green "$(served)"
reset
set_mode green no-search
deploy promote
check "no search link: the search check is skipped" 0 "$RC"
contains "output says the search check was skipped" "search check skipped" "$OUT"
reset
set_mode green bad-health
sleep 3
deploy promote
check "promote refused when the health route fails" 1 "$RC"
contains "refusal names the unhealthy colour" "not healthy" "$OUT"
check "traffic never moved (health route)" blue "$(served)"
reset
write_stub green bad-health
deploy deploy stub/app:3
check "deploy of a colour that never gets healthy fails" 1 "$RC"
check "live traffic untouched" blue "$(served)"
check "the unhealthy colour was stopped" 0 "$(running green)"

echo "--- automatic rollback after the switch"
reset
(sleep 1; set_mode green bad-health) &
bg=$!
PROMOTE_WATCH=12 deploy promote
wait "$bg"
check "promote exits non-zero when the new colour fails" 1 "$RC"
contains "output says it rolled back" "rolled back: blue is live again" "$OUT"
contains "output says what failed" "failed after the switch" "$OUT"
check "traffic is back on blue" blue "$(served)"
check "state is back on blue" blue "$(active)"
check "the failed colour was not removed" 1 "$(running green)"
check "a log line was written" 1 "$(grep -c auto-rollback "$STATE/history.log")"

echo "--- image pruning (own image repo, never the real hadith-search images)"
reset
REPO=rollback-prune-$$
export IMAGE_REPO=$REPO
trap '"$REAL_DOCKER" rm -f "$NET-hold" >/dev/null 2>&1 || true; "$REAL_DOCKER" images --format "{{.Repository}}:{{.Tag}}" "$REPO" | xargs -r "$REAL_DOCKER" rmi >/dev/null 2>&1 || true; for n in blue green edge; do "$REAL_DOCKER" rm -f "$NET-$n" >/dev/null 2>&1 || true; done; "$REAL_DOCKER" network rm "$NET" >/dev/null 2>&1 || true; rm -rf "$T"' EXIT
for n in 1 2 3 4 5; do "$REAL_DOCKER" commit "$NET-blue" "$REPO:r$n" >/dev/null; done
tags() { "$REAL_DOCKER" images --format '{{.Tag}}' "$REPO" | sort | tr '\n' ' '; }
check "five release images exist" "r1 r2 r3 r4 r5 " "$(tags)"
deploy prune-images --dry-run
check "prune --dry-run exits 0" 0 "$RC"
contains "dry run says what it would remove" "would remove $REPO:r1" "$OUT"
check "prune --dry-run removed nothing" "r1 r2 r3 r4 r5 " "$(tags)"
printf 'BLUE_REL_ID=1\nBLUE_REL_IMAGE=%s:r1\nGREEN_REL_ID=2\nGREEN_REL_IMAGE=%s:r5\nLAST=deploy\n' "$REPO" "$REPO" >"$STATE/releases.env"
deploy prune-images
check "prune exits 0" 0 "$RC"
check "keeps the newest 3 and the image releases.env names" "r1 r3 r4 r5 " "$(tags)"
"$REAL_DOCKER" run -d --name "$NET-hold" --entrypoint sleep "$REPO:r3" 300 >/dev/null
printf 'BLUE_REL_ID=4\nBLUE_REL_IMAGE=%s:r4\nGREEN_REL_ID=5\nGREEN_REL_IMAGE=%s:r5\nLAST=deploy\n' "$REPO" "$REPO" >"$STATE/releases.env"
deploy prune-images --keep 2
check "a running container's image is protected, the rest is pruned" "r3 r4 r5 " "$(tags)"
deploy prune-images --keep 0
check "--keep 0 is refused" 1 "$RC"
unset IMAGE_REPO

echo "--- schema warning"
reset
deploy promote
touch "$STATE/schema-step2-applied"
deploy rollback
check "rollback still works" 0 "$RC"
contains "rollback warns that the database is not rolled back" "step 2/2b" "$OUT"
exit $fail
