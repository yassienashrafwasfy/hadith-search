#!/usr/bin/env bash
# Checks nginx/default.conf against a stub app: syntax, the sign-in limit (5 quick tries, then
# 429 as problem+json with Retry-After), that sign-up shares it, that other routes are not
# limited, and the blue/green routing file written by tools/deploy.sh (live colour, canary
# stickiness, reload). Needs Docker; does not build the real app image.
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE=nginxinc/nginx-unprivileged:stable-alpine
NET=nginx-test-$$
PORT=${NGINX_TEST_PORT:-18080}
STUB_CONF=$(mktemp)
ROUTING_DIR=$(mktemp -d)
trap 'docker rm -f "$NET-blue" "$NET-green" "$NET-edge" >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true; rm -rf "$STUB_CONF" "$ROUTING_DIR"' EXIT

cat >"$STUB_CONF" <<'CONF'
server {
    listen 8000;
    location = /api/v1/tokens { default_type application/json; return 401 '{"detail":"stub"}'; }
    location / { default_type application/json; return 200 '{"ok":true}'; }
}
CONF

chmod 644 "$STUB_CONF"
docker network create "$NET" >/dev/null
tools/deploy.sh routing-conf blue 0 >"$ROUTING_DIR/routing.conf"
chmod 755 "$ROUTING_DIR"
chmod 644 "$ROUTING_DIR/routing.conf"
for colour in blue green; do
  docker run -d --name "$NET-$colour" --network "$NET" --network-alias "app-$colour" \
    -v "$STUB_CONF:/etc/nginx/conf.d/default.conf:ro" "$IMAGE" >/dev/null
done
docker run -d --name "$NET-edge" --network "$NET" -p "$PORT:8080" \
  -v "$PWD/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro" \
  -v "$PWD/nginx/proxy_app.conf:/etc/nginx/proxy_app.conf:ro" \
  -v "$ROUTING_DIR:/etc/nginx/routing:ro" "$IMAGE" >/dev/null
for _ in $(seq 1 20); do curl -s -o /dev/null "http://127.0.0.1:$PORT/" && break; sleep 0.5; done

fail=0
check() { # description expected actual
  if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: expected $2, got $3"; fail=1; fi
}
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
URL=http://127.0.0.1:$PORT

codes=$(for _ in 1 2 3 4 5 6 7; do code -X POST "$URL/api/v1/tokens"; echo -n " "; done)
check "sign-in: 5 through to the app, then 429" "401 401 401 401 401 429 429 " "$codes"

body=$(curl -s -i -X POST "$URL/api/v1/tokens")
check "429 is problem+json" 1 "$(echo "$body" | grep -ci '^content-type: application/problem+json')"
check "429 has Retry-After" 1 "$(echo "$body" | grep -ci '^retry-after: 60')"
check "429 body is the API error shape" 1 "$(echo "$body" | grep -c '"status":429')"

check "sign-up shares the same counter" 429 "$(code -X POST "$URL/api/v1/annotators")"

others=$(for _ in $(seq 1 30); do code "$URL/api/v1"; echo; done | sort -u | tr -d '\n')
check "other routes are not limited" 200 "$others"
check "profile route is not limited" 200 "$(code "$URL/api/v1/annotators/me")"
check "server version hidden" 0 "$(curl -sI "$URL/api/v1" | grep -ci '^server: nginx/')"
big=$(head -c 2000000 /dev/zero | tr '\0' a)
check "bodies over 1 MB rejected" 413 "$(echo "$big" | curl -s -o /dev/null -w '%{http_code}' -X POST --data-binary @- "$URL/api/v1")"

# --- blue/green routing ---
release() { curl -s -o /dev/null -D - "$URL/api/v1" | tr -d '\r' | sed -n 's/^[Xx]-[Rr]elease: //p'; }
reload_routing() { # active canary
  tools/deploy.sh routing-conf "$1" "$2" >"$ROUTING_DIR/routing.conf"
  docker exec "$NET-edge" nginx -t >/dev/null 2>&1 && docker exec "$NET-edge" nginx -s reload >/dev/null
  sleep 1
}
check "live colour is blue at first" blue "$(release)"
reload_routing green 0
check "after promote, everything goes to green" green "$(for _ in 1 2 3 4 5; do release; done | sort -u | tr -d '\n')"
reload_routing blue 0
check "after rollback, everything goes to blue" blue "$(for _ in 1 2 3 4 5; do release; done | sort -u | tr -d '\n')"
reload_routing blue 50
check "a canary keeps one client address on one colour" 1 "$(for _ in $(seq 1 20); do release; done | sort -u | wc -l | tr -d ' ')"
reload_routing blue 99
reload_routing green 99
check "canary is valid for either live colour" 1 "$(for _ in 1 2 3 4 5 6; do release; done | sort -u | wc -l | tr -d ' ')"
exit $fail
