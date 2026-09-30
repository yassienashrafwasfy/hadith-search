#!/usr/bin/env bash
# Checks nginx/default.conf against a stub app: syntax, the sign-in limit (5 quick tries, then
# 429 as problem+json with Retry-After), that sign-up shares it, and that other routes are
# not limited. Needs Docker; does not build the real app image.
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE=nginxinc/nginx-unprivileged:stable-alpine
NET=nginx-test-$$
PORT=${NGINX_TEST_PORT:-18080}
STUB_CONF=$(mktemp)
trap 'docker rm -f "$NET-app" "$NET-edge" >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true; rm -f "$STUB_CONF"' EXIT

cat >"$STUB_CONF" <<'CONF'
server {
    listen 8000;
    location = /api/v1/tokens { default_type application/json; return 401 '{"detail":"stub"}'; }
    location / { default_type application/json; return 200 '{"ok":true}'; }
}
CONF

chmod 644 "$STUB_CONF"
docker network create "$NET" >/dev/null
docker run -d --name "$NET-app" --network "$NET" --network-alias hadith-search \
  -v "$STUB_CONF:/etc/nginx/conf.d/default.conf:ro" "$IMAGE" >/dev/null
docker run -d --name "$NET-edge" --network "$NET" -p "$PORT:8080" \
  -v "$PWD/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro" \
  -v "$PWD/nginx/proxy_app.conf:/etc/nginx/proxy_app.conf:ro" "$IMAGE" >/dev/null
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
exit $fail
