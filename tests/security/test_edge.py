"""The public edge: what the site shows to the internet, with or without Cloudflare in front.

Found by the Cloudflare-oriented audit (docs/HANDOFF.md item 50). These read the nginx and compose
files, so they run everywhere; tools/test-nginx.sh proves the same rules against a real nginx.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
NGINX = ROOT / "nginx"
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
SERVICES = COMPOSE["services"]

# https://www.cloudflare.com/ips-v4 and /ips-v6, copied 2026-10-31. A change here is a decision:
# compare with those pages before editing, and never add a range that is not Cloudflare's.
CLOUDFLARE_RANGES = {
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
    "2400:cb00::/32",
    "2606:4700::/32",
    "2803:f800::/32",
    "2405:b500::/32",
    "2405:8100::/32",
    "2a06:98c0::/29",
    "2c0f:f248::/32",
}


def _strip(text: str) -> str:
    return re.sub(r"#.*", "", text)


def _conf() -> str:
    return _strip((NGINX / "default.conf").read_text())


def _realip() -> str:
    return _strip((NGINX / "cloudflare_realip.conf").read_text())


def _block(conf: str, start: str) -> str:
    """The text of the `{ ... }` that opens at `start`, with nested braces counted."""
    begin = conf.index(start)
    depth = 0
    for position in range(conf.index("{", begin), len(conf)):
        depth += {"{": 1, "}": -1}.get(conf[position], 0)
        if depth == 0:
            return conf[begin : position + 1]
    raise AssertionError(f"unclosed block: {start}")


# ---------- the visitor's address behind Cloudflare ----------


def test_the_real_address_is_trusted_from_cloudflare_ranges_and_nowhere_else():
    """With any other trusted range a client could name its own address and dodge every limit."""
    trusted = set(re.findall(r"^\s*set_real_ip_from\s+(\S+);", _realip(), re.M))
    assert trusted == CLOUDFLARE_RANGES


def test_no_catch_all_range_is_trusted():
    realip = _realip()
    for everything in ("0.0.0.0/0", "::/0", "all"):
        assert not re.search(rf"set_real_ip_from\s+{re.escape(everything)};", realip)


def test_only_the_header_cloudflare_sets_is_read():
    realip = _realip()
    assert re.search(r"^\s*real_ip_header\s+CF-Connecting-IP;", realip, re.M)
    # recursive on would also walk X-Forwarded-For, which any client can write
    assert re.search(r"^\s*real_ip_recursive\s+off;", realip, re.M)
    assert "X-Forwarded-For" not in realip and "proxy_protocol" not in realip


def test_the_server_uses_the_cloudflare_file_and_compose_mounts_it():
    server = _block(_conf(), "server {")
    assert "include /etc/nginx/cloudflare_realip.conf;" in server
    assert "./nginx/cloudflare_realip.conf:/etc/nginx/cloudflare_realip.conf:ro" in (
        SERVICES["nginx"]["volumes"]
    )


def test_every_limit_counts_the_address_after_the_real_ip_step():
    """$binary_remote_addr follows the real-IP step, so one change there fixes all the limits;
    a limit keyed on a header or on $http_x_forwarded_for would not follow it."""
    keys = re.findall(r"limit_(?:req|conn)_zone\s+(\S+)", _conf())
    assert keys and set(keys) <= {"$binary_remote_addr", "$grafana_login_key"}


def test_the_backend_is_told_the_address_nginx_settled_on():
    for text in (_conf(), _strip((NGINX / "proxy_app.conf").read_text())):
        for header in re.findall(r"proxy_set_header\s+X-(?:Real-IP|Forwarded-For)\s+(\S+);", text):
            assert header == "$remote_addr"


# ---------- who can reach the origin ----------


def test_only_nginx_publishes_a_port():
    """The app, PostgreSQL, the backups, Prometheus, Grafana, Loki and Alloy are reached over the
    compose network only; a published port would bypass nginx (and Cloudflare)."""
    published = {name for name, service in SERVICES.items() if service.get("ports")}
    assert published == {"nginx"}
    assert len(SERVICES["nginx"]["ports"]) == 1


def test_the_published_port_can_be_bound_to_localhost_for_a_tunnel():
    # HTTP_BIND=127.0.0.1 leaves a tunnel or a local proxy as the only way in
    assert SERVICES["nginx"]["ports"] == ["${HTTP_BIND:-0.0.0.0}:8000:8080"]


def test_the_internal_status_port_is_not_published():
    conf = _conf()
    status = conf[conf.index("listen 8081;") :]
    assert "stub_status;" in status and "proxy_pass" not in status
    published = "".join(SERVICES["nginx"]["ports"])
    assert "8081" not in published


def test_metrics_are_not_served_from_outside():
    block = _block(_conf(), "location = /metrics")
    assert "return 404;" in block and "proxy_pass" not in block


# ---------- the monitoring routes that are public ----------


def test_the_grafana_sign_in_is_limited_and_anonymous_access_is_off():
    login = _block(_conf(), "location = /grafana/login")
    assert "limit_req zone=grafana_login" in login and "limit_req_status 429;" in login
    env = SERVICES["grafana"]["environment"]
    assert "GF_AUTH_ANONYMOUS_ENABLED=false" in env and "GF_USERS_ALLOW_SIGN_UP=false" in env
    assert "test -n" in " ".join(SERVICES["grafana"]["entrypoint"])  # no password, no start


def test_grafana_cookies_can_be_marked_secure_for_https():
    env = SERVICES["grafana"]["environment"]
    assert any(item.startswith("GF_SECURITY_COOKIE_SECURE=${GRAFANA_COOKIE_SECURE") for item in env)


def test_the_browser_telemetry_route_takes_post_only_small_and_limited():
    block = _block(_conf(), "location = /faro/collect")
    assert "limit_except POST { deny all; }" in block
    assert "client_max_body_size 64k;" in block
    assert "limit_req zone=faro" in block and "limit_req_status 429;" in block


# ---------- what the app hands out ----------


async def test_links_are_paths_so_a_tls_terminator_or_a_forged_host_cannot_change_them(
    _api, _alice
):
    """Behind Cloudflare the app sees plain http and nginx's Host. Absolute links built from
    either would send browsers to http:// (mixed content) or to a host the client chose."""
    for path in ("/api/v1", "/api/v1/hadiths/1", "/api/v1/kv-pairs"):
        response = await _api.get(
            path,
            headers={
                **_alice["headers"],
                "Host": "evil.example",
                "X-Forwarded-Host": "evil.example",
            },
        )
        hrefs = re.findall(r'"href":\s*"([^"]*)"', response.text)
        assert all(href.startswith("/api/v1") for href in hrefs), (path, hrefs)
        assert "evil.example" not in response.text
