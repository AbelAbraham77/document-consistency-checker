"""Lazy DB setup keeps /health usable when PostgreSQL is not configured."""

from fastapi import HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from app.database.session import create_database_engine, create_session_factory


def session_factory(request: Request):
    application = request.app
    with application.state.database_lock:
        factory = getattr(application.state, "session_factory", None)
        if factory is None:
            try:
                engine = create_database_engine(application.state.settings)
            except (ValueError, SQLAlchemyError):
                raise HTTPException(503, "Database configuration is unavailable") from None
            if engine is None:
                raise HTTPException(503, "Configure DATABASE_URL and apply migrations")
            application.state.database_engine = engine
            factory = create_session_factory(engine)
            application.state.session_factory = factory
    return factory


def get_session(request: Request):
    try:
        with session_factory(request)() as session:
            yield session
    except SQLAlchemyError:
        raise HTTPException(503, "Database unavailable; check connection and migrations") from None
