"""Opt-in database factory; the API does not invoke it yet."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings


def create_database_engine(settings: Settings) -> Engine | None:
    """Return an unconnected PostgreSQL engine, or None when unconfigured.

    Accept standard PostgreSQL URLs, including those supplied by Supabase,
    and select the Psycopg 3 driver explicitly. Preserve SSL query options.
    """
    raw_url = settings.database_url.get_secret_value().strip()
    if not raw_url:
        return None

    url = make_url(raw_url)
    if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
        raise ValueError("DATABASE_URL must be a PostgreSQL URL using Psycopg 3")
    url = url.set(drivername="postgresql+psycopg")
    return create_engine(url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build a session factory; callers own session and transaction lifetimes."""
    return sessionmaker(bind=engine, expire_on_commit=False)
