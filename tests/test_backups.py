"""Backups and disaster-recovery drills (docs/HANDOFF.md item 49).

The restores themselves are drilled against a real stack by tools/dr/drill.py (needs Docker, not
run here). These tests pin the configuration the drills measured and the pieces that can be
checked without Docker: the WAL lookup used during a restore, the restore script's arguments,
and how the drill turns its observations into RTO and RPO.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
BACKUP = ROOT / "deploy" / "backup"
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
SERVICES = COMPOSE["services"]


def _drill():
    spec = importlib.util.spec_from_file_location("drill", ROOT / "tools" / "dr" / "drill.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["drill"] = module  # dataclasses look the module up while the class is built
    spec.loader.exec_module(module)
    return module


class _ComposeLoader(yaml.SafeLoader):
    """Reads compose files with their merge tags (!reset, !override) as plain values."""


_ComposeLoader.add_multi_constructor(
    "!",
    lambda loader, suffix, node: (
        loader.construct_sequence(node)
        if isinstance(node, yaml.SequenceNode)
        else loader.construct_scalar(node)
    ),
)


# ---------- configuration ----------


def test_postgres_keeps_wal_for_the_stream_but_never_more_than_2gb():
    command = " ".join(SERVICES["postgres"]["command"])
    assert "hba_file=/etc/postgresql/pg_hba.conf" in command
    assert "max_slot_wal_keep_size=2GB" in command


def test_postgres_mounts_the_hba_file_the_restore_helper_and_the_backups():
    volumes = SERVICES["postgres"]["volumes"]
    assert "./deploy/backup/pg_hba.conf:/etc/postgresql/pg_hba.conf:ro" in volumes
    assert "./deploy/backup:/backup:ro" in volumes
    assert "postgres-backups:/backups" in volumes


def test_postgres_health_is_checked_every_second_while_it_starts():
    # tools/db-restore.sh waits for healthy; at the 5 s interval that wait was most of the RTO.
    check = SERVICES["postgres"]["healthcheck"]
    assert check["start_interval"] == "1s" and check["start_period"]


def test_db_backup_uses_the_server_image_and_its_own_volume():
    backup = SERVICES["db-backup"]
    assert backup["image"] == SERVICES["postgres"]["image"]  # same pg_basebackup as the server
    assert "postgres-backups:/backups" in backup["volumes"]
    assert backup["restart"] == "unless-stopped"
    assert "postgres-backups" in COMPOSE["volumes"]


def test_deploy_init_starts_the_backups():
    assert "up -d postgres db-backup app-blue nginx" in (ROOT / "tools" / "deploy.sh").read_text()


def test_hba_allows_replication_with_a_password_only():
    rules = [line.split() for line in (BACKUP / "pg_hba.conf").read_text().splitlines()]
    rules = [rule for rule in rules if rule and not rule[0].startswith("#")]
    remote = [rule for rule in rules if rule[0] == "host" and rule[3] == "all"]
    assert ["host", "replication", "all", "all", "scram-sha-256"] in remote
    assert all(rule[-1] == "scram-sha-256" for rule in remote)  # never trust from the network


def test_drill_stack_extends_the_real_services_and_publishes_only_on_localhost():
    drill = yaml.load((ROOT / "deploy" / "dr" / "compose.drill.yml").read_text(), _ComposeLoader)
    for name in ("postgres", "db-backup", "app-blue", "nginx"):
        service = drill["services"][name]
        assert service["extends"] == {"file": "../../docker-compose.yml", "service": name}
        assert service["container_name"].startswith("hadith-dr-")
        assert all(str(port).startswith("127.0.0.1:") for port in service.get("ports", []))


@pytest.mark.parametrize("script", ["backup-loop.sh", "restore-wal.sh"])
def test_backup_scripts_parse(script):
    shell = "bash" if script.endswith("loop.sh") else "sh"
    assert subprocess.run([shell, "-n", str(BACKUP / script)]).returncode == 0
    assert os.access(BACKUP / script, os.X_OK)


# ---------- restore-wal.sh (the restore_command) ----------


def _restore_wal(wal_dir, name, dest):
    return subprocess.run(
        [str(BACKUP / "restore-wal.sh"), name, str(dest)],
        env={**os.environ, "WAL_DIR": str(wal_dir)},
    ).returncode


@pytest.fixture
def _wal(tmp_path):
    (tmp_path / "tl1").mkdir()
    (tmp_path / "tl2").mkdir()
    (tmp_path / "tl1" / "000000010000000000000003").write_text("complete tl1")
    (tmp_path / "tl1" / "000000010000000000000004.partial").write_text("partial tl1")
    (tmp_path / "tl2" / "00000002.history").write_text("history")
    (tmp_path / "tl2" / "000000020000000000000004").write_text("complete tl2")
    (tmp_path / "tl2" / "000000020000000000000005").write_text("complete")
    (tmp_path / "tl2" / "000000020000000000000005.partial").write_text("stale partial")
    return tmp_path


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("000000010000000000000003", "complete tl1"),
        ("000000010000000000000004", "partial tl1"),  # the newest segment of a timeline
        ("00000002.history", "history"),  # found in another timeline's folder
        ("000000020000000000000005", "complete"),  # a complete file wins over a partial one
    ],
)
def test_restore_wal_finds_each_file_in_any_timeline_folder(_wal, tmp_path, name, content):
    dest = tmp_path / "out"
    assert _restore_wal(_wal, name, dest) == 0
    assert dest.read_text() == content


def test_restore_wal_fails_for_a_file_it_does_not_have(_wal, tmp_path):
    # Recovery asks for files past the end of the WAL; failing is how it learns it is done.
    assert _restore_wal(_wal, "000000020000000000000099", tmp_path / "out") == 1
    assert not (tmp_path / "out").exists()


# ---------- db-restore.sh arguments ----------


def test_restore_refuses_an_unknown_argument():
    result = subprocess.run([str(ROOT / "tools" / "db-restore.sh"), "--wipe"], capture_output=True)
    assert result.returncode == 2


def test_restore_help_describes_the_steps():
    result = subprocess.run(
        [str(ROOT / "tools" / "db-restore.sh"), "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "--target-time" in result.stdout and "read-only" in result.stdout


# ---------- the drill's arithmetic ----------


def test_p95_is_the_nearest_rank():
    drill = _drill()
    assert drill.p95(list(range(1, 21))) == 19  # 20 runs: the 19th smallest
    assert drill.p95([5.0]) == 5.0
    assert drill.p95([3, 1, 2]) == 3


def test_rto_runs_from_the_fault_and_rpo_counts_only_writes_acknowledged_before_it():
    drill = _drill()
    watch = drill.Watch()
    watch.acked.update({1: 100.0, 2: 101.0, 3: 102.0, 4: 104.0, 5: 109.0})
    surviving = {1, 2, 5}  # 3 and 4 were acknowledged and are gone; 4 came after the fault
    result = drill.measure(watch, fault=103.0, start=103.0, healthy=110.5, seqs=surviving)
    assert result["rto_s"] == 7.5
    assert result["rpo_s"] == 1.0  # the oldest lost write before the fault was 1 s before it
    assert result["lost_writes"] == 1 and result["lost_after_fault"] == 1


def test_nothing_lost_means_rpo_zero():
    drill = _drill()
    watch = drill.Watch()
    watch.acked.update({1: 1.0, 2: 2.0})
    result = drill.measure(watch, fault=3.0, start=3.0, healthy=4.0, seqs={1, 2})
    assert result == {"rto_s": 1.0, "rpo_s": 0.0, "lost_writes": 0, "lost_after_fault": 0}


def test_every_scenario_is_listed_with_a_description():
    drill = _drill()
    assert set(drill.SCENARIOS) == {
        "app-kill",
        "db-kill",
        "nginx-kill",
        "db-partition",
        "app-freeze",
        "power-loss",
        "db-volume-loss",
        "drop-table",
    }
    assert all(scenario.about for scenario in drill.SCENARIOS.values())


def test_restore_picks_a_base_backup_by_when_it_finished():
    # A base backup that started before the target but finished after it cannot stop at the
    # target; the drill hit this when a DROP TABLE came while a base backup was running.
    script = (ROOT / "tools" / "db-restore.sh").read_text()
    pick = script[script.index("pick_base() {") : script.index("BASE=$(pick_base)")]
    assert "backup_manifest" in pick and '"$end" -lt "$TARGET_EPOCH"' in pick


def test_restore_keeps_few_compressed_copies_of_the_replaced_data():
    script = (ROOT / "tools" / "db-restore.sh").read_text()
    assert "zstd -1 -T0" in script and "PRE_RESTORE_KEEP" in script
