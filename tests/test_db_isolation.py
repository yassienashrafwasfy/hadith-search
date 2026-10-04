"""The DB fixture rolls back what a test leaves open, so tests cannot affect each other."""

import contextlib
import uuid

from conftest import PgStatActivity, _rollback_open_transactions
from sqlalchemy import DDL, create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool


def _tagged_engine(url, tag):
    return create_engine(
        make_url(url).update_query_dict({"application_name": tag}), poolclass=NullPool
    )


def _open_connections(admin, tag):
    with admin.connect() as conn:
        return conn.execute(
            select(func.count()).where(PgStatActivity.c.application_name == tag)
        ).scalar()


def test_open_transaction_is_rolled_back_and_its_lock_released(_pg_ready):
    tag = f"t_{uuid.uuid4().hex[:12]}"
    admin = create_engine(_pg_ready, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(DDL(f'CREATE SCHEMA "{tag}"'))
        conn.execute(DDL(f'CREATE TABLE "{tag}".t (x int)'))
    leaked = _tagged_engine(_pg_ready, tag).connect()
    leaked.execute(text(f'INSERT INTO "{tag}".t VALUES (1)'))  # no commit: transaction stays open
    assert _open_connections(admin, tag) == 1

    assert _rollback_open_transactions(admin, tag) == 1

    assert _open_connections(admin, tag) == 0
    with admin.connect() as conn:  # would block on the leaked lock if it were still held
        conn.execute(DDL(f'DROP SCHEMA "{tag}" CASCADE'))
    with contextlib.suppress(Exception):  # the server already ended this connection
        leaked.close()
    admin.dispose()


def test_other_connections_are_left_alone(_pg_ready):
    tag = f"t_{uuid.uuid4().hex[:12]}"
    other = f"t_{uuid.uuid4().hex[:12]}"
    admin = create_engine(_pg_ready, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    kept = _tagged_engine(_pg_ready, other).connect()
    kept.execute(text("SELECT 1"))
    assert _rollback_open_transactions(admin, tag) == 0
    assert _open_connections(admin, other) == 1
    kept.close()
    admin.dispose()


def test_each_test_gets_its_own_empty_schema(_pg_schema):
    assert _pg_schema.startswith("t_")
