"""Alembic environment; only online commands connect to a database."""

from alembic import context

from app.config import get_settings
from app.database.base import Base
from app.database.session import create_database_engine
import app.models  # noqa: F401 -- register all mapped tables


def run(connection):
    context.configure(
        connection=connection, target_metadata=Base.metadata, compare_type=True,
        version_table_schema=context.config.attributes.get("version_table_schema"),
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(dialect_name="postgresql", target_metadata=Base.metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    supplied_connection = context.config.attributes.get("connection")
    if supplied_connection is not None:
        run(supplied_connection)
    else:
        engine = create_database_engine(get_settings())
        if engine is None:
            raise RuntimeError("Set DATABASE_URL before running an online migration.")
        try:
            with engine.connect() as connection:
                run(connection)
        finally:
            engine.dispose()
