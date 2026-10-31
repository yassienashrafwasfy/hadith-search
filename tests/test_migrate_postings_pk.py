"""tools/migrate_postings_pk.sh: the dry run only prints, its key matches the model, and it
upgrades a database whose postings key has no INCLUDE (a throwaway schema, never a shared one)."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import DDL, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from models import Posting

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "migrate_postings_pk.sh"


def _run(*args, env=None):
    return subprocess.run(
        [str(SCRIPT), *args], capture_output=True, text=True, env={**os.environ, **(env or {})}
    )


def _covered(conn) -> bool:
    return conn.execute(
        text(
            "SELECT indnatts > indnkeyatts FROM pg_index"
            " WHERE indexrelid = 'postings_pkey'::regclass"
        )
    ).scalar()


def test_dry_run_prints_every_step_and_needs_no_database():
    result = _run("--dry-run", env={"DATABASE_URL": "", "PSQL": "false"})
    assert result.returncode == 0, result.stderr
    assert "CREATE UNIQUE INDEX CONCURRENTLY postings_pkey_covering" in result.stdout
    assert "PRIMARY KEY USING INDEX postings_pkey_covering" in result.stdout


def test_an_unknown_flag_is_refused():
    assert _run("--wipe").returncode == 2


def test_without_a_url_it_refuses_to_run():
    result = _run(env={"DATABASE_URL": ""})
    assert result.returncode != 0 and "DATABASE_URL" in result.stderr


def test_script_key_is_the_one_the_model_declares():
    ddl = str(CreateTable(Posting.__table__).compile(dialect=postgresql.dialect()))
    declared = re.search(r"PRIMARY KEY (\(.*?\) INCLUDE \(.*?\))", ddl).group(1)
    scripted = re.search(r"ON postings (\(.*?\) INCLUDE \(.*?\))", SCRIPT.read_text()).group(1)
    assert scripted == declared


@pytest.mark.skipif(shutil.which("psql") is None, reason="psql is not installed")
async def test_it_upgrades_a_key_without_include(_pg_ready, _patched_paths):
    import database

    with database.get_sync_engine().begin() as conn:  # make the key look like an old release's
        conn.execute(
            DDL(
                "ALTER TABLE postings DROP CONSTRAINT postings_pkey,"
                " ADD PRIMARY KEY (language, term, hadith_id)"
            )
        )
        assert not _covered(conn)
    url = os.environ["DATABASE_URL"].replace("+psycopg", "")
    result = _run(env={"DATABASE_URL": url})
    assert result.returncode == 0, result.stdout + result.stderr
    again = _run(env={"DATABASE_URL": url})  # and it can be repeated
    assert again.returncode == 0 and "nothing to do" in again.stdout, again.stdout + again.stderr
    with database.get_sync_engine().connect() as conn:
        assert _covered(conn)
