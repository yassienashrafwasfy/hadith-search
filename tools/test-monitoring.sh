#!/usr/bin/env bash
# Checks the monitoring configs (docs/HANDOFF.md item 47) with each tool's own validator, using the
# images pinned in docker-compose.yml: Prometheus config and alert rules, the alert unit tests in
# monitoring/prometheus/alerts.test.yml (every threshold fires just above its limit and not below),
# the Alertmanager template as entrypoint.sh fills it, Loki, Alloy, blackbox and the Grafana
# dashboard JSON. Needs Docker. Run it after editing anything under monitoring/.
set -euo pipefail
cd "$(dirname "$0")/.."

image() { # service name -> image pinned in docker-compose.yml
  awk -v svc="  $1:" '$0 == svc {found=1; next} found && /image:/ {print $2; exit}' docker-compose.yml
}

fail=0
run() { # description, command...
  local name=$1
  shift
  if out=$("$@" 2>&1); then
    echo "ok   $name"
  else
    echo "FAIL $name"
    echo "$out" | tail -20 | sed 's/^/     /'
    fail=1
  fi
}

M="$PWD/monitoring"
run "prometheus.yml" docker run --rm -v "$M/prometheus:/p:ro" -w /p --entrypoint promtool \
  "$(image prometheus)" check config --syntax-only prometheus.yml
run "alerts.yml" docker run --rm -v "$M/prometheus:/p:ro" -w /p --entrypoint promtool \
  "$(image prometheus)" check rules alerts.yml
run "alert thresholds (promtool unit tests)" docker run --rm -v "$M/prometheus:/p:ro" -w /p \
  --entrypoint promtool "$(image prometheus)" test rules alerts.test.yml
# shellcheck disable=SC2016  # $ signs are for the shell inside the container
run "alertmanager.yml from the template" docker run --rm \
  -e SMTP_HOST=smtp.example.com:587 -e SMTP_USERNAME=me@example.com -e 'SMTP_PASSWORD=p&s|w\d' \
  -e ALERT_EMAIL_TO=you@example.com -v "$M/alertmanager:/etc/alertmanager:ro" \
  --entrypoint sh "$(image alertmanager)" -c \
  'sed "s|^exec .*|amtool check-config /tmp/alertmanager/alertmanager.yml \&\& test \"\$(cat /tmp/alertmanager/smtp_password)\" = \"\$SMTP_PASSWORD\"|" /etc/alertmanager/entrypoint.sh > /tmp/check.sh && sh /tmp/check.sh'
run "alertmanager refuses to start without SMTP settings" sh -c \
  "! docker run --rm -v '$M/alertmanager:/etc/alertmanager:ro' --entrypoint sh '$(image alertmanager)' /etc/alertmanager/entrypoint.sh"
run "loki.yml" docker run --rm -v "$M/loki:/etc/loki:ro" "$(image loki)" \
  -config.file=/etc/loki/loki.yml -verify-config
run "config.alloy" docker run --rm -v "$M/alloy:/etc/alloy:ro" "$(image alloy)" \
  validate /etc/alloy/config.alloy
run "blackbox.yml" docker run --rm -v "$M/blackbox:/etc/blackbox:ro" "$(image blackbox)" \
  --config.file=/etc/blackbox/blackbox.yml --config.check
run "Grafana dashboards are valid JSON" python3 -c '
import json, pathlib, sys
for path in pathlib.Path(sys.argv[1]).glob("*.json"):
    dash = json.loads(path.read_text())
    assert dash["uid"] and dash["panels"], path
' "$M/grafana/dashboards"

exit $fail
