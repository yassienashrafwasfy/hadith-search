"""tools/migrate_trgm.sh: the dry run only prints, its indexes match the models, and it works
on a database made before the trigram indexes existed (a throwaway schema, never a shared one)."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import DDL, create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateIndex

from models import Base

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "migrate_trgm.sh"
TRGM_INDEXES = {
    index.name: index
    for table in Base.metadata.tables.values()
    for index in table.indexes
    if index.name.endswith("_trgm")
}


def _run(*args, env=None):
    return subprocess.run(
        [str(SCRIPT), *args], capture_output=True, text=True, env={**os.environ, **(env or {})}
    )


def test_dry_run_prints_every_step_and_needs_no_database():
    result = _run("--dry-run", env={"DATABASE_URL": "", "PSQL": "false"})
    assert result.returncode == 0, result.stderr
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in result.stdout
    for name in TRGM_INDEXES:
        assert f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} " in result.stdout


def test_an_unknown_flag_is_refused():
    assert _run("--wipe").returncode == 2


def test_without_a_url_it_refuses_to_run():
    result = _run(env={"DATABASE_URL": ""})
    assert result.returncode != 0 and "DATABASE_URL" in result.stderr


@pytest.mark.parametrize("name", sorted(TRGM_INDEXES))
def test_script_indexes_are_the_ones_the_models_declare(name):
    declared = str(CreateIndex(TRGM_INDEXES[name]).compile(dialect=postgresql.dialect()))
    declared = re.sub(r"\s+", " ", declared).replace("CREATE INDEX", "").strip()
    line = next(row for row in SCRIPT.read_text().splitlines() if f"EXISTS {name} " in row)
    scripted = re.search(r"EXISTS (.*)\"", line).group(1)
    assert scripted == declared


@pytest.mark.skipif(shutil.which("psql") is None, reason="psql is not installed")
async def test_it_upgrades_a_database_that_has_no_trigram_objects(_pg_ready, _patched_paths):
    import database

    admin = create_engine(_pg_ready, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    schema = make_url(os.environ["DATABASE_URL"]).query["application_name"]
    url = os.environ["DATABASE_URL"].replace("+psycopg", "")
    with database.get_sync_engine().begin() as conn:  # make the schema look like an old release
        for name in TRGM_INDEXES:
            conn.execute(DDL(f"DROP INDEX IF EXISTS {name}"))
    result = _run(env={"DATABASE_URL": url, "PYTHON": sys.executable})
    assert result.returncode == 0, result.stdout + result.stderr
    again = _run(env={"DATABASE_URL": url, "PYTHON": sys.executable})  # and it can be repeated
    assert again.returncode == 0, again.stdout + again.stderr
    with admin.connect() as conn:
        found = {
            row[0]
            for row in conn.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = :s"), {"s": schema}
            )
        }
    admin.dispose()
    assert TRGM_INDEXES.keys() <= found
