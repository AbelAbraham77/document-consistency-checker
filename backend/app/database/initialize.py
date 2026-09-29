"""Apply versioned migrations: python -m app.database.initialize [--sql]."""

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config


def migration_config() -> Config:
    backend = Path(__file__).resolve().parents[2]
    return Config(str(backend / "alembic.ini"))


def initialize_database(*, sql: bool = False) -> None:
    """Upgrade to head, or emit PostgreSQL SQL without connecting when sql=True."""
    command.upgrade(migration_config(), "head", sql=sql)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Initialize/upgrade the database using Alembic.")
    parser.add_argument("--sql", action="store_true", help="Print migration SQL without connecting")
    args = parser.parse_args(argv)
    initialize_database(sql=args.sql)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
