"""ASGI entry point: uvicorn app.main:app --reload."""

from contextlib import asynccontextmanager
import logging
from collections.abc import AsyncIterator
from functools import partial
from threading import Lock

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.documents import router as documents_router
from app.jobs.executor import InProcessExecutor
from app.jobs.worker import DocumentJobWorker
from app.pipeline import process_document
from app.config import Settings, get_settings
from app.logging_config import configure_logging
from app.storage import create_document_storage

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level)
        logger.info("Starting %s (%s)", settings.app_name, settings.environment)
        try:
            yield
        finally:
            application.state.job_executor.close()
            engine = getattr(application.state, "database_engine", None)
            if engine is not None:
                engine.dispose()
            logger.info("Stopping %s", settings.app_name)

    application = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    application.state.settings = settings
    application.state.document_storage = create_document_storage(settings)
    application.state.database_lock = Lock()
    application.state.processor = partial(process_document, settings=settings)
    application.state.job_executor = InProcessExecutor(DocumentJobWorker(
        lambda: application.state.session_factory,
        lambda path, document_id: application.state.processor(path, document_id),
        lambda: application.state.document_storage,
    ), workers=settings.processing_workers)
    application.include_router(health_router)
    application.include_router(documents_router)
    return application


app = create_app()
