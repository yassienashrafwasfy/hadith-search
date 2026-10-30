"""The monitoring files stay consistent with each other (docs/HANDOFF.md item 47).

tools/test-monitoring.sh runs the real validators (promtool, amtool, ...) in Docker; these checks
need nothing but Python, so they run with the rest of the suite.
"""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MONITORING = ROOT / "monitoring"


def _rules() -> list[dict]:
    groups = yaml.safe_load((MONITORING / "prometheus" / "alerts.yml").read_text())["groups"]
    return [rule for group in groups for rule in group["rules"]]


def test_every_alert_has_a_severity_and_a_summary():
    for rule in _rules():
        assert rule["labels"]["severity"] in {"critical", "warning"}, rule["alert"]
        assert rule["annotations"]["summary"], rule["alert"]


def test_every_alert_has_a_promtool_test():
    tests = yaml.safe_load((MONITORING / "prometheus" / "alerts.test.yml").read_text())["tests"]
    tested = {case["alertname"] for test in tests for case in test["alert_rule_test"]}
    untested = {rule["alert"] for rule in _rules()} - tested
    assert not untested


def test_the_dashboard_queries_only_known_datasources():
    dash = json.loads((MONITORING / "grafana" / "dashboards" / "hadith-overview.json").read_text())
    provisioned = yaml.safe_load(
        (MONITORING / "grafana" / "provisioning" / "datasources" / "datasources.yml").read_text()
    )
    uids = {source["uid"] for source in provisioned["datasources"]}
    used = {panel["datasource"]["uid"] for panel in dash["panels"] if "datasource" in panel}
    assert used <= uids


def test_compose_pins_every_monitoring_image():
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    for name in ("prometheus", "alertmanager", "grafana", "loki", "alloy", "cadvisor", "blackbox"):
        tag = services[name]["image"].rsplit(":", 1)[1]
        assert tag[0].isdigit() or tag[0] == "v", (name, tag)
        assert tag != "latest"
