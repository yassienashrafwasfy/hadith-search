#!/usr/bin/env bash
# Blue/green releases and canaries on one host (docker compose + nginx).
#
#   tools/deploy.sh init                 first start: postgres, nginx and the blue app
#   tools/deploy.sh status               which colour is live, canary share, health of each
#   tools/deploy.sh deploy [--build|IMG] start the idle colour with a new image and wait for health
#   tools/deploy.sh canary PERCENT       send PERCENT% of client addresses (1-99) to the idle colour
#   tools/deploy.sh promote              send everything to the idle colour (old one keeps running)
#   tools/deploy.sh rollback [--force]   canary: stop it. After a promote: switch back to the old colour
#   tools/deploy.sh stop-idle            stop the colour that gets no traffic (frees memory)
#   tools/deploy.sh prune-images [--keep N]  remove old hadith-search release images, keep the newest N (3)
#
# Add --dry-run to deploy, canary, promote, rollback, stop-idle or prune-images to print what would happen and
# change nothing. promote checks the new colour after the switch (nginx serves it, GET /api/v1/health
# and one search answer, container stays healthy for PROMOTE_WATCH seconds) and switches back by
# itself, exit 1, if any check fails. Every release has an id (the git sha for --build) and an
# image tag that is never overwritten; deploy/state/releases.env records what each colour runs.
# prune-images never touches an image a container is using or that releases.env names (live and
# previous colour), never uses `docker image prune`, and only looks at the hadith-search repository.
# PRUNE_AFTER_PROMOTE=1 runs it after every successful promote (default off).
# The database is NOT rolled back (docs/HANDOFF.md, item 35). If the owner has applied the
# destructive schema steps (item 28, step 2/2b), create deploy/state/schema-step2-applied (or set
# SCHEMA_STEP2_APPLIED=1) and rollback prints a warning.
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
REL_FILE=$STATE_DIR/releases.env
HISTORY_FILE=$STATE_DIR/history.log
PROMOTE_WATCH=${PROMOTE_WATCH:-$CANARY_WATCH} # seconds the new colour is watched after promote
SMOKE_HEALTH_PATH=${SMOKE_HEALTH_PATH:-/api/v1/health}
SMOKE_SEARCH=${SMOKE_SEARCH:-1}               # 0 skips the search request in the smoke check
# Empty means: use the search below if GET /api/v1 lists "searches", else skip the search check.
# term-overlap is on in every APP_MODE that has search, needs no model and answers 200 with no
# results on an empty or unrelated corpus, so a healthy release cannot fail it.
SMOKE_SEARCH_PATH=${SMOKE_SEARCH_PATH:-}
SMOKE_RETRY_WAIT=${SMOKE_RETRY_WAIT:-2}       # seconds before a busy (503) search is tried once more
SMOKE_DEFAULT_SEARCH='/api/v1/searches?q=prayer&method=term-overlap&lang=en'
KEEP_IMAGES=${KEEP_IMAGES:-3}                 # release tags prune-images keeps (newest first)
PRUNE_AFTER_PROMOTE=${PRUNE_AFTER_PROMOTE:-0} # 1 runs prune-images after a successful promote
IMAGE_REPO=${IMAGE_REPO:-hadith-search}       # repository whose release tags prune-images may remove
COMPOSE=(docker compose)

# --dry-run and --force can stand anywhere on the command line.
DRY_RUN=0 FORCE=0
ARGS=()
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --force) FORCE=1 ;;
    *) ARGS+=("$arg") ;;
  esac
done
set -- "${ARGS[@]+"${ARGS[@]}"}"

die() { echo "error: $*" >&2; exit 1; }
log() { echo "$(date -u +%H:%M:%S) deploy: $*"; }
say_dry() { log "dry run: would $*"; }
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

rel_get() { sed -n "s/^$1=//p" "$REL_FILE" 2>/dev/null | tail -1; } # KEY -> VALUE (empty if unknown)

rel_set() { # KEY VALUE [KEY VALUE ...]: update releases.env atomically
  mkdir -p "$STATE_DIR"
  touch "$REL_FILE"
  local tmp="$REL_FILE.tmp"
  cp "$REL_FILE" "$tmp"
  while [ $# -ge 2 ]; do
    { grep -v "^$1=" "$tmp" || true; echo "$1=$2"; } >"$tmp.2"
    mv "$tmp.2" "$tmp"
    shift 2
  done
  mv "$tmp" "$REL_FILE"
}

colour_key() { echo "$1" | tr '[:lower:]' '[:upper:]'; } # blue -> BLUE
release_of() { local r; r=$(rel_get "$(colour_key "$1")_REL_ID"); echo "${r:-unknown}"; }
image_of() { local r; r=$(rel_get "$(colour_key "$1")_REL_IMAGE"); echo "${r:-unknown}"; }

history() { # words...: one line per event, kept for the owner
  mkdir -p "$STATE_DIR"
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >>"$HISTORY_FILE"
}

# The database is shared and is not rolled back. Warn if the owner says the column drops ran.
warn_schema() {
  if [ "${SCHEMA_STEP2_APPLIED:-0}" = 1 ] || [ -f "$STATE_DIR/schema-step2-applied" ]; then
    {
      echo "WARNING: the destructive schema step (HANDOFF item 28, step 2/2b) is marked as applied."
      echo "WARNING: the old release may not run on this database. Rollback does not touch the database."
      echo "WARNING: if the old release fails, restore the pg_dump taken before step 2 (docs/HANDOFF.md, item 35)."
    } >&2
  fi
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

# Run inside the nginx container, which is on the same network as both colours.
# smoke_code prints the HTTP status (nothing if the connection failed or timed out).
smoke_code() {
  "${COMPOSE[@]}" exec -T nginx sh -c \
    'wget -S -q -T 10 -O /dev/null "$1" 2>&1 | grep -i "^ *HTTP/" | tail -1 | awk "{print \$2}"' _ "$1" || true
}
smoke_body() { "${COMPOSE[@]}" exec -T nginx sh -c 'wget -q -T 10 -O - "$1"' _ "$1" || true; }

# Asks the colour directly (not through nginx). Sets SMOKE_ERR on failure.
#  - GET $SMOKE_HEALTH_PATH must answer 200 (the app is up and its database answers).
#  - One search must answer 200. Zero results is fine. A 503 (the queue is busy) is tried once
#    more and then only warned about. Any other answer, or none, fails. Skipped when
#    SMOKE_SEARCH=0, or when the API root does not list "searches" (a deployment without search).
smoke() { # colour
  local base="http://app-$1:8000" code path=$SMOKE_SEARCH_PATH
  SMOKE_ERR=""
  code=$(smoke_code "$base$SMOKE_HEALTH_PATH")
  if [ "$code" != 200 ]; then
    SMOKE_ERR="GET $SMOKE_HEALTH_PATH on $1 answered ${code:-nothing}, expected 200"
    return 1
  fi
  [ "$SMOKE_SEARCH" = 1 ] || return 0
  if [ -z "$path" ]; then
    case "$(smoke_body "$base/api/v1")" in
      *'"searches"'*) path=$SMOKE_DEFAULT_SEARCH ;;
      *) log "smoke: $1 does not offer search; search check skipped"; return 0 ;;
    esac
  fi
  code=$(smoke_code "$base$path")
  if [ "$code" = 503 ]; then
    sleep "$SMOKE_RETRY_WAIT"
    code=$(smoke_code "$base$path")
    if [ "$code" = 503 ]; then
      log "warning: search on $1 answered 503 (busy) twice; not counted as a failure" >&2
      return 0
    fi
  fi
  if [ "$code" != 200 ]; then
    SMOKE_ERR="search request ($path) on $1 answered ${code:-nothing}, expected 200"
    return 1
  fi
}

# Asks nginx itself which colour answers, so a switch that did not land is noticed.
edge_serves() { # colour
  local tries=0
  until "${COMPOSE[@]}" exec -T nginx sh -c \
    'wget -S -q -T 5 -O /dev/null "http://127.0.0.1:8080$1" 2>&1 | grep -qi "x-release: $2"' _ "$SMOKE_HEALTH_PATH" "$1"; do
    tries=$((tries + 1))
    [ "$tries" -lt 5 ] || return 1
    sleep 1
  done
}

# Checks run after traffic moved to a colour. Prints the reason and returns 1 on failure.
post_switch_checks() { # colour
  edge_serves "$1" || { SMOKE_ERR="nginx is not serving $1 after the reload"; return 1; }
  smoke "$1" || return 1
  local waited=0
  while [ "$waited" -lt "$PROMOTE_WATCH" ]; do
    sleep 3
    waited=$((waited + 3))
    [ "$(health "$1")" = healthy ] || { SMOKE_ERR="$1 became $(health "$1") while watched"; return 1; }
  done
  [ "$PROMOTE_WATCH" -eq 0 ] || smoke "$1"
}

# 1.2.3 -> "1.2.3"; registry/app:2 -> "2"; a name without a tag is its own id.
release_from_image() { local tag=${1##*/}; if [[ "$tag" == *:* ]]; then echo "${tag##*:}"; else echo "$tag"; fi; }

# The git sha, with "-dirty-<time>" when the working tree has changes, so a tag is never reused.
make_release_id() {
  local sha
  sha=$(git rev-parse --short=12 HEAD 2>/dev/null) || { date -u +%Y%m%d%H%M%S; return; }
  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then echo "$sha-dirty-$(date -u +%Y%m%d%H%M%S)"; else echo "$sha"; fi
}

image_var() { echo "$(echo "$1" | tr '[:lower:]' '[:upper:]')_IMAGE"; }

cmd_init() {
  mkdir -p "$STATE_DIR"
  [ ! -f "$STATE_FILE" ] || die "already initialised (see: tools/deploy.sh status)"
  local image=${BLUE_IMAGE:-hadith-search:blue}
  if [ "$DRY_RUN" -eq 1 ]; then say_dry "start postgres, nginx and blue ($image)"; return; fi
  save_state blue 0 ""
  routing_conf blue 0 >"$ROUTING_FILE"
  rel_set BLUE_REL_ID "$(release_from_image "$image")" BLUE_REL_IMAGE "$image" LAST init
  history "init blue $image"
  "${COMPOSE[@]}" --profile blue up -d postgres app-blue nginx
  wait_healthy blue || die "blue did not become healthy in ${HEALTH_TIMEOUT}s (docker compose logs app-blue)"
  log "blue is live ($image)."
}

cmd_status() {
  load_state
  local idle
  idle=$(other "$ACTIVE")
  echo "live:     $ACTIVE (health: $(health "$ACTIVE"), release: $(release_of "$ACTIVE"), image: $(image_of "$ACTIVE"))"
  echo "idle:     $idle (health: $(health "$idle"), release: $(release_of "$idle"), image: $(image_of "$idle"))"
  echo "canary:   ${CANARY}% of client addresses go to $idle"
  echo "previous: ${PREVIOUS:-none}$([ -n "$PREVIOUS" ] && echo " (rollback target)")"
  echo "last:     $(rel_get LAST)"
  warn_schema
}

cmd_deploy() {
  load_state
  [ "$CANARY" -eq 0 ] || die "a canary is running; promote or roll it back first"
  local idle image release build=0
  idle=$(other "$ACTIVE")
  case "${1:-}" in
    --build) build=1; release=$(make_release_id); image="hadith-search:$release" ;;
    "") die "usage: tools/deploy.sh deploy --build | IMAGE" ;;
    *) image=$1; release=$(release_from_image "$image") ;;
  esac
  [[ "$release" =~ ^[A-Za-z0-9._-]+$ ]] || die "release id '$release' has characters other than letters, digits, . _ -"
  if [ "$DRY_RUN" -eq 1 ]; then
    [ "$build" -eq 0 ] || say_dry "build $image (skipped if that tag already exists; tags are never overwritten)"
    say_dry "replace $idle (now release $(release_of "$idle")) with release $release ($image), wait for healthy; live traffic stays on $ACTIVE"
    return
  fi
  # The model the idle colour starts with (written by tools/promote_model.sh); only the colour
  # being created reads it, the running one keeps the environment it was created with.
  if [ -f "$STATE_DIR/model.env" ]; then
    set -a; . "$STATE_DIR/model.env"; set +a
    log "model settings from $STATE_DIR/model.env: ARABIC_MODEL_DIR=${ARABIC_MODEL_DIR:-} EMBEDDINGS_RELEASE=${EMBEDDINGS_RELEASE:-}"
  fi
  if [ "$build" -eq 1 ]; then
    if docker image inspect "$image" >/dev/null 2>&1; then
      log "image $image already exists; not rebuilding (tags are never overwritten)"
    else
      log "building $image"
      docker build --pull --build-arg "REVISION=$release" --build-arg "CREATED=$(date -u +%Y-%m-%dT%H:%M:%SZ)" -t "$image" .
    fi
  fi
  # The idle colour is the rollback target after a promote; replacing it ends that window. Its
  # image stays on disk under its own tag, so it can be started again with: deploy IMAGE.
  if [ -n "$PREVIOUS" ]; then
    log "replacing $idle (release $(release_of "$idle")); rollback to it by name is no longer available."
    log "its image stays on disk: tools/deploy.sh deploy $(image_of "$idle")"
  fi
  save_state "$ACTIVE" 0 ""
  rel_set "$(colour_key "$idle")_REL_ID" "$release" "$(colour_key "$idle")_REL_IMAGE" "$image" LAST deploy
  history "deploy $idle $release $image"
  export "$(image_var "$idle")=$image"
  "${COMPOSE[@]}" --profile "$idle" up -d --force-recreate --no-deps "app-$idle"
  if ! wait_healthy "$idle"; then
    "${COMPOSE[@]}" --profile "$idle" stop "app-$idle" >/dev/null
    die "$idle did not become healthy in ${HEALTH_TIMEOUT}s; stopped it, live traffic untouched"
  fi
  log "$idle is healthy and idle (release $release, $image). Next: tools/deploy.sh canary 10   or   promote"
}

cmd_canary() {
  load_state
  local percent=${1:-}
  [[ "$percent" =~ ^[0-9]+$ ]] && [ "$percent" -ge 1 ] && [ "$percent" -le 99 ] ||
    die "usage: tools/deploy.sh canary PERCENT (1-99)"
  local idle
  idle=$(other "$ACTIVE")
  require_healthy "$idle"
  if [ "$DRY_RUN" -eq 1 ]; then say_dry "send ${percent}% of client addresses to $idle and watch it for ${CANARY_WATCH}s"; return; fi
  apply_routing "$ACTIVE" "$percent"
  save_state "$ACTIVE" "$percent" "$PREVIOUS"
  log "canary: ${percent}% of client addresses now go to $idle. Watching it for ${CANARY_WATCH}s..."
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
  log "$idle stayed healthy. Raise it with 'canary N', finish with 'promote', or undo with 'rollback'."
}

# Switch traffic back to $1 after a failed promote. Nothing is stopped or removed.
auto_rollback() { # old new reason
  local old=$1 new=$2
  {
    echo "error: release $(release_of "$new") on $new failed after the switch: $3"
    echo "error: automatic rollback to $old (release $(release_of "$old"))..."
  } >&2
  if [ "$(health "$old")" != healthy ]; then
    echo "error: $old is not healthy either (state: $(health "$old")). Traffic left on $new. Investigate now:" >&2
    echo "error:   docker compose logs app-$old app-$new" >&2
    exit 1
  fi
  apply_routing "$old" 0
  save_state "$old" 0 "$new"
  rel_set LAST rollback
  history "auto-rollback $new->$old reason: $3"
  warn_schema
  echo "error: rolled back: $old is live again; $new is still running for inspection (docker compose logs app-$new). Exit 1." >&2
  exit 1
}

cmd_promote() {
  load_state
  local idle
  idle=$(other "$ACTIVE")
  require_healthy "$idle"
  if [ "$DRY_RUN" -eq 1 ]; then
    say_dry "smoke-test $idle, switch all traffic from $ACTIVE to $idle (release $(release_of "$idle")), check it for ${PROMOTE_WATCH}s, and switch back by itself on failure"
    return
  fi
  # Check the new colour before it takes traffic as well; a failure here changes nothing.
  smoke "$idle" || die "$idle failed the smoke check before the switch ($SMOKE_ERR); traffic not moved"
  local old=$ACTIVE
  log "switching traffic $old -> $idle (release $(release_of "$idle"))"
  apply_routing "$idle" 0
  save_state "$idle" 0 "$old"
  rel_set LAST promote
  history "promote $old->$idle release $(release_of "$idle")"
  if ! post_switch_checks "$idle"; then auto_rollback "$old" "$idle" "$SMOKE_ERR"; fi
  log "$idle is live (release $(release_of "$idle")). $old still runs for a quick rollback; stop it with: tools/deploy.sh stop-idle"
  if [ "$PRUNE_AFTER_PROMOTE" = 1 ]; then cmd_prune_images || log "prune-images failed; the promote itself succeeded" >&2; fi
}

cmd_rollback() {
  load_state
  if [ "$CANARY" -gt 0 ]; then
    if [ "$DRY_RUN" -eq 1 ]; then say_dry "stop the canary; all traffic stays on $ACTIVE"; return; fi
    apply_routing "$ACTIVE" 0
    save_state "$ACTIVE" 0 "$PREVIOUS"
    log "canary stopped; all traffic is on $ACTIVE."
    return
  fi
  [ -n "$PREVIOUS" ] || die "nothing to roll back to: no previous colour is kept (no promote yet, or a deploy or stop-idle replaced it). To go back to an older image: tools/deploy.sh deploy IMAGE, then promote"
  if [ "$(rel_get LAST)" = rollback ] && [ "$FORCE" -eq 0 ]; then
    die "already rolled back ($ACTIVE is live). To go forward again use 'promote'; to flip anyway use 'rollback --force'"
  fi
  local state
  state=$(health "$PREVIOUS")
  case "$state" in
    healthy) ;;
    none) die "cannot roll back: $PREVIOUS is not running (container missing or stopped). Start it with: tools/deploy.sh deploy $(image_of "$PREVIOUS"); traffic not moved" ;;
    *) die "cannot roll back: $PREVIOUS is not healthy (state: $state); traffic not moved" ;;
  esac
  smoke "$PREVIOUS" || die "cannot roll back: $PREVIOUS failed the smoke check ($SMOKE_ERR); traffic not moved"
  warn_schema
  if [ "$DRY_RUN" -eq 1 ]; then
    say_dry "switch traffic $ACTIVE (release $(release_of "$ACTIVE")) -> $PREVIOUS (release $(release_of "$PREVIOUS")); $ACTIVE keeps running"
    return
  fi
  local bad=$ACTIVE
  log "rolling back: $bad (release $(release_of "$bad")) -> $PREVIOUS (release $(release_of "$PREVIOUS"))"
  apply_routing "$PREVIOUS" 0
  save_state "$PREVIOUS" 0 "$bad"
  rel_set LAST rollback
  history "rollback $bad->$ACTIVE release $(release_of "$ACTIVE")"
  edge_serves "$ACTIVE" || echo "warning: nginx did not confirm $ACTIVE after the reload; check: tools/deploy.sh status" >&2
  log "rolled back: $ACTIVE is live again. $bad still runs; the database was not touched."
}

# Removes old release images of $IMAGE_REPO. Keeps the newest N by creation time, plus any image
# a running container uses and any image releases.env names (the live and the previous colour).
# Only release tags count: the compose defaults :blue and :green and :latest are left alone.
cmd_prune_images() {
  local keep=$KEEP_IMAGES
  while [ $# -gt 0 ]; do
    case "$1" in
      --keep) keep=${2:-}; shift 2 || die "usage: tools/deploy.sh prune-images [--keep N] [--dry-run]" ;;
      *) die "usage: tools/deploy.sh prune-images [--keep N] [--dry-run]" ;;
    esac
  done
  [[ "$keep" =~ ^[0-9]+$ ]] && [ "$keep" -ge 1 ] || die "--keep must be a whole number of at least 1"
  local ref created ordered=() protected
  while IFS= read -r ref; do
    [ -n "$ref" ] || continue
    case "${ref##*:}" in blue | green | latest | "<none>") continue ;; esac
    created=$(docker image inspect -f '{{.Created}}' "$ref" 2>/dev/null) || continue
    ordered+=("$(date -u -d "$created" +%s.%N 2>/dev/null || echo 0) $ref")
  done < <(docker images --format '{{.Repository}}:{{.Tag}}' "$IMAGE_REPO")
  if [ "${#ordered[@]}" -le "$keep" ]; then
    log "prune-images: ${#ordered[@]} release image(s) of $IMAGE_REPO, keeping $keep; nothing to remove"
    return
  fi
  protected=$(
    docker ps --format '{{.Image}}'
    rel_get BLUE_REL_IMAGE
    rel_get GREEN_REL_IMAGE
  )
  local line removed=0 position=0
  while IFS= read -r line; do
    position=$((position + 1))
    ref=${line#* }
    [ "$position" -gt "$keep" ] || continue
    if grep -qxF -- "$ref" <<<"$protected"; then
      log "prune-images: keeping $ref (a container uses it or releases.env names it)"
      continue
    fi
    if [ "$DRY_RUN" -eq 1 ]; then say_dry "remove $ref"; continue; fi
    if docker rmi "$ref" >/dev/null; then
      log "prune-images: removed $ref"
      history "prune-images removed $ref"
      removed=$((removed + 1))
    else
      log "prune-images: could not remove $ref (left in place)" >&2
    fi
  done < <(printf '%s\n' "${ordered[@]}" | sort -rn)
  [ "$DRY_RUN" -eq 1 ] || log "prune-images: removed $removed image(s); kept the newest $keep and the protected ones"
}

cmd_stop_idle() {
  load_state
  [ "$CANARY" -eq 0 ] || die "a canary is running; roll it back or promote first"
  local idle
  idle=$(other "$ACTIVE")
  if [ "$DRY_RUN" -eq 1 ]; then say_dry "stop $idle (release $(release_of "$idle")); rollback is no longer possible, its image stays on disk"; return; fi
  "${COMPOSE[@]}" --profile "$idle" stop "app-$idle"
  save_state "$ACTIVE" 0 ""
  log "$idle stopped; its image ($(image_of "$idle")) stays on disk."
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
  prune-images) shift; cmd_prune_images "$@" ;;
  *) sed -n '2,13p' "$0"; exit 1 ;;
esac
