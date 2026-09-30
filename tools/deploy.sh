#!/usr/bin/env bash
# Blue/green releases and canaries on one host (docker compose + nginx).
#
#   tools/deploy.sh init                 first start: postgres, nginx and the blue app
#   tools/deploy.sh status               which colour is live, canary share, health of each
#   tools/deploy.sh deploy [--build|IMG] start the idle colour with a new image and wait for health
#   tools/deploy.sh canary PERCENT       send PERCENT% of client addresses (1-99) to the idle colour
#   tools/deploy.sh promote              send everything to the idle colour (old one keeps running)
#   tools/deploy.sh rollback             canary: stop it. After a promote: switch back to the old colour
#   tools/deploy.sh stop-idle            stop the colour that gets no traffic (frees memory)
#
# Traffic is split by a hash of the client address, so one client stays on one colour for the
# whole canary. Traffic only moves to a colour whose container reports healthy. Both colours
# share one database, so schema changes must be backward compatible (docs/HANDOFF.md, item 21).
# `tools/deploy.sh routing-conf ACTIVE CANARY` prints the nginx routing file and does nothing else.
set -euo pipefail
cd "$(dirname "$0")/.."

STATE_DIR=deploy/state
STATE_FILE=$STATE_DIR/state.env
ROUTING_FILE=$STATE_DIR/routing.conf
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-240}  # seconds to wait for a new colour to become healthy
CANARY_WATCH=${CANARY_WATCH:-30}       # seconds the canary colour is watched after traffic moves
COMPOSE=(docker compose)

die() { echo "error: $*" >&2; exit 1; }
other() { [ "$1" = blue ] && echo green || echo blue; }

# Prints the nginx routing file. CANARY is the percent of client addresses sent to the other colour.
routing_conf() {
  local active=$1 canary=$2 idle
  idle=$(other "$active")
  echo "# Written by tools/deploy.sh. Do not edit."
  echo "split_clients \"\${remote_addr}\" \$app_backend {"
  if [ "$canary" -gt 0 ]; then echo "    ${canary}% app-$idle:8000;"; fi
  echo "    * app-$active:8000;"
  echo "}"
  echo "map \$app_backend \$release_colour {"
  echo "    app-blue:8000 blue;"
  echo "    default green;"
  echo "}"
}

load_state() {
  [ -f "$STATE_FILE" ] || die "no state yet; run: tools/deploy.sh init"
  # shellcheck disable=SC1090
  . "$STATE_FILE"
}

save_state() { # active canary previous
  mkdir -p "$STATE_DIR"
  printf 'ACTIVE=%s\nCANARY=%s\nPREVIOUS=%s\n' "$1" "$2" "$3" >"$STATE_FILE.tmp"
  mv "$STATE_FILE.tmp" "$STATE_FILE"
  ACTIVE=$1 CANARY=$2 PREVIOUS=$3
}

# Write the routing file atomically and tell nginx to reload it. If nginx rejects the new
# file, put the old one back so the running config and the file on disk agree.
apply_routing() {
  local previous_conf=""
  [ -f "$ROUTING_FILE" ] && previous_conf=$(cat "$ROUTING_FILE")
  routing_conf "$1" "$2" >"$ROUTING_FILE.tmp"
  mv "$ROUTING_FILE.tmp" "$ROUTING_FILE"
  if ! "${COMPOSE[@]}" exec -T nginx nginx -t >/dev/null 2>&1; then
    [ -n "$previous_conf" ] && printf '%s\n' "$previous_conf" >"$ROUTING_FILE"
    die "nginx rejected the new routing; nothing changed"
  fi
  "${COMPOSE[@]}" exec -T nginx nginx -s reload >/dev/null
}

health() { # colour -> healthy | unhealthy | starting | none
  local id
  id=$("${COMPOSE[@]}" --profile "$1" ps -q "app-$1" 2>/dev/null || true)
  [ -n "$id" ] || { echo none; return; }
  docker inspect -f '{{if .State.Running}}{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}{{else}}none{{end}}' "$id"
}

wait_healthy() { # colour
  local waited=0
  until [ "$(health "$1")" = healthy ]; do
    [ "$waited" -lt "$HEALTH_TIMEOUT" ] || return 1
    sleep 3
    waited=$((waited + 3))
  done
}

require_healthy() { # colour
  [ "$(health "$1")" = healthy ] || die "$1 is not healthy (state: $(health "$1")); traffic not moved"
}

image_var() { echo "$(echo "$1" | tr '[:lower:]' '[:upper:]')_IMAGE"; }

cmd_init() {
  mkdir -p "$STATE_DIR"
  [ ! -f "$STATE_FILE" ] || die "already initialised (see: tools/deploy.sh status)"
  save_state blue 0 ""
  routing_conf blue 0 >"$ROUTING_FILE"
  "${COMPOSE[@]}" --profile blue up -d postgres app-blue nginx
  wait_healthy blue || die "blue did not become healthy in ${HEALTH_TIMEOUT}s (docker compose logs app-blue)"
  echo "blue is live."
}

cmd_status() {
  load_state
  echo "live:     $ACTIVE (health: $(health "$ACTIVE"))"
  echo "idle:     $(other "$ACTIVE") (health: $(health "$(other "$ACTIVE")"))"
  echo "canary:   ${CANARY}% of client addresses go to $(other "$ACTIVE")"
  echo "previous: ${PREVIOUS:-none}"
}

cmd_deploy() {
  load_state
  [ "$CANARY" -eq 0 ] || die "a canary is running; promote or roll it back first"
  local idle image
  idle=$(other "$ACTIVE")
  case "${1:-}" in
    --build) image="hadith-search:$idle"; docker build --pull -t "$image" . ;;
    "") die "usage: tools/deploy.sh deploy --build | IMAGE" ;;
    *) image=$1 ;;
  esac
  export "$(image_var "$idle")=$image"
  "${COMPOSE[@]}" --profile "$idle" up -d --force-recreate --no-deps "app-$idle"
  if ! wait_healthy "$idle"; then
    "${COMPOSE[@]}" --profile "$idle" stop "app-$idle" >/dev/null
    die "$idle did not become healthy in ${HEALTH_TIMEOUT}s; stopped it, live traffic untouched"
  fi
  echo "$idle is healthy and idle ($image). Next: tools/deploy.sh canary 10   or   promote"
}

cmd_canary() {
  load_state
  local percent=${1:-}
  [[ "$percent" =~ ^[0-9]+$ ]] && [ "$percent" -ge 1 ] && [ "$percent" -le 99 ] ||
    die "usage: tools/deploy.sh canary PERCENT (1-99)"
  local idle
  idle=$(other "$ACTIVE")
  require_healthy "$idle"
  apply_routing "$ACTIVE" "$percent"
  save_state "$ACTIVE" "$percent" "$PREVIOUS"
  echo "canary: ${percent}% of client addresses now go to $idle. Watching it for ${CANARY_WATCH}s..."
  local waited=0
  while [ "$waited" -lt "$CANARY_WATCH" ]; do
    sleep 3
    waited=$((waited + 3))
    if [ "$(health "$idle")" != healthy ]; then
      apply_routing "$ACTIVE" 0
      save_state "$ACTIVE" 0 "$PREVIOUS"
      die "$idle became unhealthy during the canary; all traffic is back on $ACTIVE"
    fi
  done
  echo "$idle stayed healthy. Raise it with 'canary N', finish with 'promote', or undo with 'rollback'."
}

cmd_promote() {
  load_state
  local idle
  idle=$(other "$ACTIVE")
  require_healthy "$idle"
  apply_routing "$idle" 0
  save_state "$idle" 0 "$ACTIVE"
  echo "$idle is live. $ACTIVE still runs for a quick rollback; stop it with: tools/deploy.sh stop-idle"
}

cmd_rollback() {
  load_state
  if [ "$CANARY" -gt 0 ]; then
    apply_routing "$ACTIVE" 0
    save_state "$ACTIVE" 0 "$PREVIOUS"
    echo "canary stopped; all traffic is on $ACTIVE."
    return
  fi
  [ -n "$PREVIOUS" ] || die "nothing to roll back to"
  require_healthy "$PREVIOUS"
  apply_routing "$PREVIOUS" 0
  save_state "$PREVIOUS" 0 "$ACTIVE"
  echo "rolled back: $PREVIOUS is live again."
}

cmd_stop_idle() {
  load_state
  [ "$CANARY" -eq 0 ] || die "a canary is running; roll it back or promote first"
  local idle
  idle=$(other "$ACTIVE")
  "${COMPOSE[@]}" --profile "$idle" stop "app-$idle"
  save_state "$ACTIVE" 0 ""
}

case "${1:-}" in
  routing-conf) routing_conf "${2:?active colour}" "${3:?canary percent}" ;;
  init) cmd_init ;;
  status) cmd_status ;;
  deploy) shift; cmd_deploy "$@" ;;
  canary) shift; cmd_canary "$@" ;;
  promote) cmd_promote ;;
  rollback) cmd_rollback ;;
  stop-idle) cmd_stop_idle ;;
  *) sed -n '2,12p' "$0"; exit 1 ;;
esac
