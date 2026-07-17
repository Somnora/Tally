"""Shared fixtures: scratch Postgres databases, migrated schema, connections.

Tests that need a database are skipped (not failed) when local Postgres is
unreachable, so the pure-logic tests still run anywhere.
"""

from collections.abc import Iterator

import psycopg
import pytest

from pipeline import db, migrate

ADMIN_URL = "postgresql://localhost/postgres"
TEST_DB = "civic_test"
TEST_DB_URL = f"postgresql://localhost/{TEST_DB}"


def _recreate_database(name: str) -> None:
    try:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')  # noqa: S608
            admin.execute(f'CREATE DATABASE "{name}"')
    except psycopg.OperationalError:
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
