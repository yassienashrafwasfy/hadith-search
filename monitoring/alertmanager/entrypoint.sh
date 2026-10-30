#!/bin/sh
# Fill alertmanager.yml.tmpl from the environment, then start Alertmanager. Runs in the busybox-based
# prom/alertmanager image, so only sh and sed are available.
set -eu
: "${SMTP_HOST:?Set SMTP_HOST (host:port, e.g. smtp.gmail.com:587) in .env}"
: "${SMTP_USERNAME:?Set SMTP_USERNAME in .env}"
: "${SMTP_PASSWORD:?Set SMTP_PASSWORD in .env}"
: "${ALERT_EMAIL_TO:?Set ALERT_EMAIL_TO in .env}"
ALERT_EMAIL_FROM="${ALERT_EMAIL_FROM:-$SMTP_USERNAME}"

out=/tmp/alertmanager
mkdir -p "$out"
umask 077
printf '%s' "$SMTP_PASSWORD" > "$out/smtp_password"

# Escape the characters sed treats specially in a replacement (\, & and the | delimiter).
esc() { printf '%s' "$1" | sed 's/[\\&|]/\\&/g'; }
sed -e "s|\${SMTP_HOST}|$(esc "$SMTP_HOST")|" \
    -e "s|\${SMTP_USERNAME}|$(esc "$SMTP_USERNAME")|" \
    -e "s|\${ALERT_EMAIL_FROM}|$(esc "$ALERT_EMAIL_FROM")|" \
    -e "s|\${ALERT_EMAIL_TO}|$(esc "$ALERT_EMAIL_TO")|" \
    /etc/alertmanager/alertmanager.yml.tmpl > "$out/alertmanager.yml"

exec /bin/alertmanager --config.file="$out/alertmanager.yml" --storage.path=/alertmanager "$@"
