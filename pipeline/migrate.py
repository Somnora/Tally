"""Migration runner: applies baseline schema + numbered migrations, in order.

Usage:
    uv run python -m pipeline.migrate                      # DATABASE_URL from .env
    uv run python -m pipeline.migrate --database-url ...   # override (used by tests)

Applied files are recorded in schema_migrations with their sha256. Editing an
already-applied file is an error — write a new migration instead (or, before
any data matters, drop and recreate the database).
"""

import argparse
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import LiteralString, cast

import psycopg

from pipeline.config import get_settings

DB_DIR = Path(__file__).resolve().parent.parent / "db"

# The baseline files CLAUDE.md names, applied as migrations 0001/0002.
# Files in db/migrations/ must be numbered 0003 or higher.
BASELINE: list[tuple[str, Path]] = [
    ("0001_schema.sql", DB_DIR / "schema.sql"),
    ("0002_schema_additions.sql", DB_DIR / "schema_additions.sql"),
]

ENSURE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    sha256     TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


@dataclass(frozen=True)
class Migration:
    name: str  # recorded filename, defines ordering
    path: Path


def discover_migrations() -> list[Migration]:
    """Baseline first, then db/migrations/*.sql sorted by filename."""
    migrations = [Migration(name, path) for name, path in BASELINE]
    migrations_dir = DB_DIR / "migrations"
    for path in sorted(migrations_dir.glob("*.sql")):
        if path.name <= "0002":
            raise SystemExit(f"{path.name}: migrations must be numbered 0003 or higher")
        migrations.append(Migration(path.name, path))
    return migrations


def apply_migrations(database_url: str) -> list[str]:
    """Apply all pending migrations; return the names of those applied."""
    applied_now: list[str] = []
    with psycopg.connect(database_url) as conn:
        conn.execute(ENSURE_TABLE_SQL)
        conn.commit()

        recorded: dict[str, str] = {
            name: digest
            for name, digest in conn.execute(
                "SELECT filename, sha256 FROM schema_migrations"
            ).fetchall()
        }

        for migration in discover_migrations():
            sql_text = migration.path.read_text(encoding="utf-8")
            digest = hashlib.sha256(sql_text.encode("utf-8")).hexdigest()

            if migration.name in recorded:
                if recorded[migration.name] != digest:
                    raise SystemExit(
                        f"{migration.name} was edited after being applied "
                        f"(recorded {recorded[migration.name][:12]}, file {digest[:12]}). "
                        "Write a new migration instead."
                    )
                continue

            # One transaction per migration: it fully applies or fully rolls back.
            # Cast: migration files are static repo assets, not dynamic SQL.
            with conn.transaction():
                conn.execute(cast(LiteralString, sql_text))
                conn.execute(
                    "INSERT INTO schema_migrations (filename, sha256) VALUES (%s, %s)",
                    (migration.name, digest),
                )
            applied_now.append(migration.name)

    return applied_now


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None, help="override DATABASE_URL")
    args = parser.parse_args()

    url: str = args.database_url or get_settings().database_url
    applied = apply_migrations(url)
    if applied:
        for name in applied:
            print(f"applied  {name}")
    else:
        print("up to date — nothing to apply")
    sys.exit(0)


if __name__ == "__main__":
    main()
