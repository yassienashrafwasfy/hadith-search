"""Disaster-recovery drills: break the drill stack on purpose, recover, measure RTO and RPO.

    .venv/bin/python tools/dr/drill.py up                      # start the drill stack (and seed it)
    .venv/bin/python tools/dr/drill.py run app-kill db-kill --runs 20 --out results.json
    .venv/bin/python tools/dr/drill.py list                    # every scenario and what it does
    .venv/bin/python tools/dr/drill.py down                    # remove the drill stack and volumes

The drill stack is deploy/dr/compose.drill.yml: the real postgres, app and nginx services under
the project name `hadith-dr`, with their own containers, volumes and ports (docs/HANDOFF.md
item 49). The real `hadith-postgres` is only ever read, once, by `up --seed-from-real` (a
pg_dump of the corpus tables, no annotator data), so the drill database has the real size.

During a run, two threads watch the stack:
- a prober asks nginx for GET /api/v1/health every 50 ms (what a user sees);
- a writer inserts one row a time into `dr_writes` straight into PostgreSQL and notes the moment
  each commit is acknowledged (what the database promised to keep).

RTO is the time from the fault to the moment both are healthy again and stay healthy (the last
failed health check or write, plus one interval). RPO is how much acknowledged data is gone
after recovery: the fault time minus the time of the oldest acknowledged write before the fault
that is no longer in the table (0 when none is missing). For `drop-table` the fault is the
mistake itself, and the restore goes back to just before it.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import psycopg

ROOT = Path(__file__).resolve().parents[2]
DR_DIR = ROOT / "deploy" / "dr"
STATE = DR_DIR / "state"
ENV_FILE = STATE / "drill.env"
PROJECT = "hadith-dr"
HTTP_PORT = int(os.environ.get("DR_HTTP_PORT", "18000"))
PG_PORT = int(os.environ.get("DR_PG_PORT", "15432"))
HEALTH_URL = f"http://127.0.0.1:{HTTP_PORT}/api/v1/health"
PROBE_INTERVAL = 0.05
STEADY_SECONDS = 3.0  # healthy this long before the fault, and after recovery
RECOVERY_TIMEOUT = 600.0
CONTAINERS = {"postgres": "hadith-dr-postgres", "app": "hadith-dr-blue", "nginx": "hadith-dr-nginx"}
# Corpus tables copied from the real database by `up --seed-from-real`; never annotator data.
SEED_TABLES = [
    "hadiths",
    "postings",
    "hadith_embeddings",
    "terms",
    "hadith_lengths",
    "chapters",
    "books",
    "hadith_preprocessed",
    "hadith_exact_text",
    "embedding_sets",
]


# ---------- running things ----------


def compose_env() -> dict:
    """What docker compose and tools/db-restore.sh need to act on the drill project."""
    return {
        **os.environ,
        "COMPOSE_PROJECT_NAME": PROJECT,
        "COMPOSE_FILE": str(DR_DIR / "compose.drill.yml"),
        "COMPOSE_ENV_FILES": str(ENV_FILE),
    }


def sh(*args: str, check: bool = True, quiet: bool = True) -> subprocess.CompletedProcess:
    out = subprocess.PIPE if quiet else None
    result = subprocess.run(args, env=compose_env(), cwd=ROOT, stdout=out, stderr=out, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
    return result


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return sh("docker", "compose", *args, check=check)


def env_value(name: str) -> str:
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1]
    raise KeyError(name)


def pg_url() -> str:
    password = env_value("POSTGRES_PASSWORD")
    return f"postgresql://hadith:{password}@127.0.0.1:{PG_PORT}/hadith"


# ---------- watching ----------


@dataclass
class Watch:
    """Health checks through nginx and acknowledged writes, both stamped with time.time()."""

    probes: list[tuple[float, bool]] = field(default_factory=list)
    errors: list[float] = field(default_factory=list)  # failed writes
    acked: dict[int, float] = field(default_factory=dict)  # seq -> acknowledgement time
    stop: threading.Event = field(default_factory=threading.Event)

    def start(self) -> None:
        self.threads = [threading.Thread(target=t, daemon=True) for t in (self._probe, self._write)]
        for thread in self.threads:
            thread.start()

    def finish(self) -> None:
        self.stop.set()
        for thread in self.threads:
            thread.join(5)

    def _probe(self) -> None:
        while not self.stop.is_set():
            ok = False
            try:
                with urllib.request.urlopen(HEALTH_URL, timeout=1) as response:
                    ok = response.status == 200
            except Exception:
                pass
            self.probes.append((time.time(), ok))
            time.sleep(PROBE_INTERVAL)

    def _write(self) -> None:
        seq, conn = max(self.acked, default=0), None
        while not self.stop.is_set():
            seq += 1
            try:
                if conn is None or conn.closed:
                    conn = psycopg.connect(pg_url(), autocommit=True, connect_timeout=1)
                conn.execute("INSERT INTO dr_writes (seq) VALUES (%s)", (seq,))
                self.acked[seq] = time.time()
            except Exception:
                self.errors.append(time.time())
                conn = _close(conn)
            time.sleep(PROBE_INTERVAL)
        _close(conn)

    def healthy_since(self, now: float) -> float | None:
        """When the stack last became fully healthy, if it has been since `now - STEADY_SECONDS`."""
        failures = [t for t, ok in self.probes if not ok] + self.errors
        last_failure = max(failures, default=0.0)
        recent_ok = [t for t, ok in self.probes if ok and t > last_failure]
        recent_writes = [t for t in self.acked.values() if t > last_failure]
        if not recent_ok or not recent_writes:
            return None
        since = max(min(recent_ok), min(recent_writes))
        return since if now - since >= STEADY_SECONDS else None


def _close(conn):
    try:
        if conn is not None:
            conn.close()
    except Exception:
        pass
    return None


def wait_healthy(watch: Watch, timeout: float = RECOVERY_TIMEOUT) -> float:
    deadline = time.time() + timeout
    while time.time() < deadline:
        since = watch.healthy_since(time.time())
        if since is not None:
            return since
        time.sleep(0.2)
    raise TimeoutError(f"the stack was not healthy again within {timeout:.0f} s")


# ---------- the database probe table ----------


def ensure_probe_table() -> None:
    with psycopg.connect(pg_url(), autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS dr_writes"
            " (seq bigint PRIMARY KEY, written timestamptz NOT NULL DEFAULT clock_timestamp())"
        )
        conn.execute("TRUNCATE dr_writes")


def surviving_seqs() -> set[int]:
    with psycopg.connect(pg_url(), autocommit=True, connect_timeout=5) as conn:
        return {seq for (seq,) in conn.execute("SELECT seq FROM dr_writes")}


# ---------- scenarios ----------


@dataclass
class Scenario:
    name: str
    about: str
    inject: Callable[[], dict]  # breaks something; returns details the recovery may need
    recover: Callable[[dict], None] = lambda details: None  # what an operator (or a script) does
    clears_at: Callable[[dict], float] | None = None  # when the fault itself ended, if it ends
    check: Callable[[], None] = lambda: None  # extra checks after recovery (raise on failure)


def crash(*services: str) -> None:
    """SIGKILL each container's main process from outside, as the kernel's OOM killer would.

    `docker kill` is not a crash: Docker counts it as a manual stop and the restart policy then
    leaves the container down. A process that dies by itself is restarted, so the drill kills
    the host PID (from a throwaway container in the host PID namespace).
    """
    pids = [
        sh("docker", "inspect", "-f", "{{.State.Pid}}", CONTAINERS[s]).stdout.strip()
        for s in services
    ]
    sh("docker", "run", "--rm", "--pid=host", "alpine:3.20", "kill", "-9", *pids)


def kill(service: str) -> Callable[[], dict]:
    def inject() -> dict:
        crash(service)
        return {}

    return inject


def partition_db(seconds: float = 10.0) -> dict:
    """Cut postgres off every network for `seconds` (a switch or a link going down)."""
    networks = json.loads(
        sh(
            "docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", CONTAINERS["postgres"]
        ).stdout
    )
    for network in networks:
        sh("docker", "network", "disconnect", "-f", network, CONTAINERS["postgres"])
    time.sleep(seconds)
    healed = time.time()
    for network, settings in networks.items():
        # Put the compose names back too (the service name is a DNS alias on the network).
        aliases = [arg for alias in settings.get("Aliases") or [] for arg in ("--alias", alias)]
        sh("docker", "network", "connect", *aliases, network, CONTAINERS["postgres"])
    return {"healed": healed}


def power_loss() -> dict:
    """Every container dies at once, as when the host loses power; then the daemon starts them."""
    crash(*CONTAINERS)
    return {}


def freeze(service: str, seconds: float = 10.0) -> Callable[[], dict]:
    def inject() -> dict:
        sh("docker", "pause", CONTAINERS[service])
        time.sleep(seconds)
        healed = time.time()
        sh("docker", "unpause", CONTAINERS[service])
        return {"healed": healed}

    return inject


def lose_db_volume() -> dict:
    """The disk under the database is gone: kill postgres and wipe its data directory."""
    sh("docker", "kill", CONTAINERS["postgres"])
    volume = f"{PROJECT}_postgres-data"
    sh(
        "docker",
        "run",
        "--rm",
        "-v",
        f"{volume}:/data",
        "alpine:3.20",
        "sh",
        "-c",
        "rm -rf /data/* /data/.[!.]*",
    )
    return {}


def drop_table() -> dict:
    """Someone runs DROP TABLE on live data by mistake; the restore goes back to just before it.

    The target is the moment before the statement (what an operator reads off the log), so
    every write acknowledged before the mistake should survive and nothing after it does.
    """
    with psycopg.connect(pg_url(), autocommit=True) as conn:
        target = time.time()
        conn.execute("DROP TABLE IF EXISTS dr_precious")
    return {"target": target}


def ensure_precious() -> None:
    with psycopg.connect(pg_url(), autocommit=True) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS dr_precious (id int PRIMARY KEY)")
        conn.execute("INSERT INTO dr_precious VALUES (1) ON CONFLICT DO NOTHING")


def precious_is_back() -> None:
    with psycopg.connect(pg_url(), autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM dr_precious").fetchone()[0] == 1


def restore(details: dict) -> None:
    args = ["tools/db-restore.sh", "--yes"]
    if "target" in details:
        args += ["--target-time", f"@{details['target']:.6f}"]
    sh(*args, quiet=False)


SCENARIOS = {
    s.name: s
    for s in [
        Scenario("app-kill", "SIGKILL the app container; Docker restarts it", kill("app")),
        Scenario("db-kill", "SIGKILL PostgreSQL; crash recovery from WAL", kill("postgres")),
        Scenario("nginx-kill", "SIGKILL nginx; Docker restarts it", kill("nginx")),
        Scenario(
            "db-partition",
            "postgres off the network for 10 s, then back",
            partition_db,
            clears_at=lambda d: d["healed"],
        ),
        Scenario(
            "app-freeze",
            "app frozen (docker pause) for 10 s, then thawed",
            freeze("app"),
            clears_at=lambda d: d["healed"],
        ),
        Scenario(
            "power-loss",
            "every container killed at once, then started as on boot",
            power_loss,
        ),
        Scenario(
            "db-volume-loss",
            "PostgreSQL data directory wiped; restore from backup",
            lose_db_volume,
            recover=restore,
        ),
        Scenario(
            "drop-table",
            "DROP TABLE by mistake; point-in-time restore to just before",
            drop_table,
            recover=restore,
            check=precious_is_back,
        ),
    ]
}


# ---------- one run ----------


def measure(watch: Watch, fault: float, start: float, healthy: float, seqs: set[int]) -> dict:
    lost_before = [t for seq, t in watch.acked.items() if t < fault and seq not in seqs]
    lost_after = [t for seq, t in watch.acked.items() if t >= fault and seq not in seqs]
    return {
        "rto_s": round(max(healthy - start, 0.0), 3),
        "rpo_s": round(fault - min(lost_before), 3) if lost_before else 0.0,
        "lost_writes": len(lost_before),
        "lost_after_fault": len(lost_after),
    }


def settle(min_uptime: float = 12.0) -> None:
    """Wait until every container has been up for `min_uptime` seconds.

    Docker doubles its restart delay (100 ms, 200 ms, ... up to a minute) for a container that
    dies again within 10 s of starting, and resets it after 10 s of uptime. Crashing it again
    sooner would measure that back-off, not the recovery.
    """
    from datetime import datetime

    while True:
        started = sh("docker", "inspect", "-f", "{{.State.StartedAt}}", *CONTAINERS.values())
        oldest_ok = min(
            time.time() - datetime.fromisoformat(line.strip()).timestamp()
            for line in started.stdout.splitlines()
        )
        if oldest_ok >= min_uptime:
            return
        time.sleep(min_uptime - oldest_ok + 0.1)


def run_once(scenario: Scenario) -> dict:
    settle()
    ensure_probe_table()
    ensure_precious()
    watch = Watch()
    watch.start()
    try:
        wait_healthy(watch, timeout=120)
        fault = time.time()
        details = scenario.inject()
        scenario.recover(details)
        healthy = wait_healthy(watch)
    finally:
        watch.finish()
    scenario.check()
    start = scenario.clears_at(details) if scenario.clears_at else fault
    # For a point-in-time restore the data loss is counted from the mistake, not from the start.
    lost_from = details.get("target", fault)
    return measure(watch, lost_from, start, healthy, surviving_seqs())


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, -(-95 * len(ordered) // 100) - 1)]  # nearest rank


def summarise(name: str, runs: list[dict]) -> dict:
    rto = [r["rto_s"] for r in runs]
    rpo = [r["rpo_s"] for r in runs]
    return {
        "scenario": name,
        "runs": len(runs),
        "rto_p50_s": round(statistics.median(rto), 3),
        "rto_p95_s": round(p95(rto), 3),
        "rto_max_s": round(max(rto), 3),
        "rpo_p95_s": round(p95(rpo), 3),
        "rpo_max_s": round(max(rpo), 3),
        "lost_writes_max": max(r["lost_writes"] for r in runs),
    }


# ---------- commands ----------


def write_env() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    if ENV_FILE.exists():
        return
    ENV_FILE.write_text(
        f"POSTGRES_PASSWORD={secrets.token_hex(16)}\n"
        f"AUTH_SECRET={secrets.token_hex(32)}\n"
        "CORS_ORIGINS=http://localhost\n"
        "APP_MODE=annotation\n"
    )
    ENV_FILE.chmod(0o600)


def seed_from_real() -> None:
    """Copy the corpus tables (read only) from the real database, so restores move real sizes.

    They go into their own database, `hadith_corpus`, on the drill server: a physical backup
    copies the whole server either way, and the app's own schema in `hadith` stays as it is.
    """
    psql = [
        "docker",
        "exec",
        "-i",
        CONTAINERS["postgres"],
        "psql",
        "-q",
        "-U",
        "hadith",
        "-v",
        "ON_ERROR_STOP=1",
    ]
    sh(
        *psql,
        "-d",
        "hadith",
        "-c",
        "DROP DATABASE IF EXISTS hadith_corpus",
        "-c",
        "CREATE DATABASE hadith_corpus",
    )
    sh(
        *psql,
        "-d",
        "hadith_corpus",
        "-c",
        "CREATE EXTENSION vector",
        "-c",
        "CREATE EXTENSION pg_trgm",
    )
    tables = [arg for table in SEED_TABLES for arg in ("-t", table)]
    dump = subprocess.Popen(
        [
            "docker",
            "exec",
            "hadith-postgres",
            "pg_dump",
            "-U",
            "hadith",
            "-d",
            "hadith",
            "--no-owner",
            "--no-privileges",
            *tables,
        ],
        stdout=subprocess.PIPE,
    )
    load = subprocess.run(
        [*psql, "-d", "hadith_corpus"], stdin=dump.stdout, stdout=subprocess.DEVNULL
    )
    dump.stdout.close()  # so pg_dump gets SIGPIPE instead of waiting if psql stopped early
    if dump.wait() != 0 or load.returncode != 0:
        raise RuntimeError("seeding from hadith-postgres failed")


def wait_for_base_backup(after: float = 0.0, timeout: float = 600.0) -> None:
    """Until db-backup has finished a base backup newer than `after` (unix time)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        newest = compose(
            "exec",
            "-T",
            "db-backup",
            "sh",
            "-c",
            "stat -c %Y /backups/base/*Z/base.tar.zst 2>/dev/null | sort -n | tail -n 1",
            check=False,
        ).stdout.strip()
        if newest and float(newest) > after:
            return
        time.sleep(2)
    raise TimeoutError("db-backup did not finish a base backup")


def cmd_up(args) -> None:
    write_env()
    compose("up", "-d", "--wait")
    if args.seed_from_real:
        seed_from_real()
    ensure_probe_table()
    ensure_precious()
    # A base backup after the seed, so a restore does not have to replay the whole load.
    compose("exec", "-T", "db-backup", "touch", "/backups/.need-base")
    wait_for_base_backup(after=time.time())
    print(f"drill stack up: http://127.0.0.1:{HTTP_PORT}  postgres 127.0.0.1:{PG_PORT}")


def cmd_down(args) -> None:
    compose("down", "-v", "--remove-orphans", check=False)
    if ENV_FILE.exists():
        ENV_FILE.unlink()


def cmd_run(args) -> None:
    report = []
    for name in args.scenarios:
        runs = []
        for i in range(args.runs):
            result = run_once(SCENARIOS[name])
            runs.append(result)
            print(
                f"{name:15} run {i + 1:2}/{args.runs}  RTO {result['rto_s']:7.2f} s"
                f"  RPO {result['rpo_s']:6.2f} s  lost {result['lost_writes']}",
                flush=True,
            )
        report.append({**summarise(name, runs), "each": runs})
        print(json.dumps({k: v for k, v in report[-1].items() if k != "each"}), flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1))


def cmd_list(args) -> None:
    for scenario in SCENARIOS.values():
        print(f"{scenario.name:15} {scenario.about}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("up")
    up.add_argument("--seed-from-real", action="store_true")
    up.set_defaults(func=cmd_up)
    sub.add_parser("down").set_defaults(func=cmd_down)
    sub.add_parser("list").set_defaults(func=cmd_list)
    run = sub.add_parser("run")
    run.add_argument("scenarios", nargs="+", choices=sorted(SCENARIOS))
    run.add_argument("--runs", type=int, default=20)
    run.add_argument("--out")
    run.set_defaults(func=cmd_run)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
