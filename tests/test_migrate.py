"""Tests for the migration runner: apply, no-op re-run, edit guard."""

import shutil
from pathlib import Path

import psycopg
import pytest

from pipeline import migrate


def test_apply_then_noop(scratch_db: str) -> None:
    applied = migrate.apply_migrations(scratch_db)
    assert applied == ["0001_schema.sql", "0002_schema_additions.sql"]

    # Second run: everything is recorded, nothing re-applies.
    assert migrate.apply_migrations(scratch_db) == []

    with psycopg.connect(scratch_db) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            ).fetchall()
        }
    assert {"politicians", "promises", "sources", "schema_migrations"} <= tables


def test_edited_applied_migration_is_refused(scratch_db: str, tmp_path: Path) -> None:
    repo_db_dir = Path(migrate.DB_DIR)
    shutil.copy(repo_db_dir / "schema.sql", tmp_path / "schema.sql")
    shutil.copy(repo_db_dir / "schema_additions.sql", tmp_path / "schema_additions.sql")

    migrate.apply_migrations(scratch_db, db_dir=tmp_path)

    # Editing an already-applied file must halt the runner with a clear error.
    with (tmp_path / "schema.sql").open("a", encoding="utf-8") as f:
        f.write("\n-- sneaky edit after apply\n")
    with pytest.raises(SystemExit, match="edited after being applied"):
        migrate.apply_migrations(scratch_db, db_dir=tmp_path)


def test_misnumbered_migration_is_refused(scratch_db: str, tmp_path: Path) -> None:
    repo_db_dir = Path(migrate.DB_DIR)
    shutil.copy(repo_db_dir / "schema.sql", tmp_path / "schema.sql")
    shutil.copy(repo_db_dir / "schema_additions.sql", tmp_path / "schema_additions.sql")
    (tmp_path / "migrations").mkdir()
    # Colliding with the baseline numbering must be rejected up front.
    (tmp_path / "migrations" / "0001_bad.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(SystemExit, match="0003 or higher"):
        migrate.apply_migrations(scratch_db, db_dir=tmp_path)
