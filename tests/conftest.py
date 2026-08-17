"""Shared fixtures: scratch Postgres databases, migrated schema, connections.

Tests that need a database are skipped (not failed) when local Postgres is
unreachable, so the pure-logic tests still run anywhere.

Except under CI, where that leniency would be a lie: most of this suite needs
a database, so a runner without one would skip nearly everything and still
report a green check. When CI is set, an unreachable Postgres is a failure.
"""

import os
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql

from pipeline import db, migrate

ADMIN_URL = "postgresql://localhost/postgres"
TEST_DB = "civic_test"
TEST_DB_URL = f"postgresql://localhost/{TEST_DB}"


def _recreate_database(name: str) -> None:
    # A database name cannot be a bound parameter, so it is composed as an
    # identifier instead of interpolated into the string. psycopg's types
    # reject a plain f-string here on purpose, and quoting it correctly is
    # cheaper than teaching the checker to ignore the one place we do it.
    database = sql.Identifier(name)
    try:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(database))
            admin.execute(sql.SQL("CREATE DATABASE {}").format(database))
    except psycopg.OperationalError as exc:
        if os.environ.get("CI"):
            raise RuntimeError(
                "Postgres is unreachable and CI is set. Skipping here would let "
                "the run report success while testing almost nothing."
            ) from exc
        pytest.skip("local Postgres is not reachable")


@pytest.fixture(scope="session")
def migrated_db_url() -> str:
    """A scratch database with the full schema applied once per test session."""
    _recreate_database(TEST_DB)
    migrate.apply_migrations(TEST_DB_URL)
    return TEST_DB_URL


@pytest.fixture
def conn(migrated_db_url: str) -> Iterator[db.Connection]:
    """A connection whose work is rolled back after each test."""
    connection = db.connect(migrated_db_url)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


@pytest.fixture
def scratch_db() -> Iterator[str]:
    """A fresh, EMPTY scratch database (for migration-runner tests)."""
    name = "civic_test_migrations"
    _recreate_database(name)
    yield f"postgresql://localhost/{name}"
