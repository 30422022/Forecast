from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


@pytest.fixture
def pg_database():
    dsn = os.getenv("GRIDCAST_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("set GRIDCAST_TEST_DATABASE_URL to run PostgreSQL integration tests")
    schema = "gridcast_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as db:
        db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        yield dsn, schema
    finally:
        with psycopg.connect(dsn, autocommit=True) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
