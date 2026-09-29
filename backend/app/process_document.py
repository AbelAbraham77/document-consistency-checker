"""CLI: python -m app.process_document sample.pdf [--document-id UUID]."""

import argparse
import logging
from pathlib import Path
import sys
from uuid import UUID
from sqlalchemy import select

from app.config import Settings
from app.database.repositories import create_document
from app.database.session import create_database_engine, create_session_factory
from app.pipeline import process_document
from app.schemas.persistence import DocumentCreate
from app.storage import create_document_storage
from app.models import Document


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Process a PDF or resume a document by ID")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--document-id", type=UUID, help="Resume an existing document")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    document_id = args.document_id
    try:
        path = args.pdf.expanduser().resolve()
        if not path.is_file() or path.suffix.lower() != ".pdf":
            raise ValueError("PDF path is invalid")
        if document_id is None:
            settings = Settings()
            engine = create_database_engine(settings)
            if engine is None:
                raise ValueError("DATABASE_URL is required")
            try:
                storage = create_document_storage(settings)
                reference = storage.save(path)
                try:
                    with create_session_factory(engine).begin() as session:
                        document_id = create_document(session, DocumentCreate(filename=path.name, storage_path=reference)).id
                except Exception:
                    storage.delete(reference)
                    raise
            finally:
                engine.dispose()
        else:
            engine = create_database_engine(Settings())
            if engine is None:
                raise ValueError("DATABASE_URL is required")
            try:
                with create_session_factory(engine)() as session:
                    if session.scalar(select(Document.id).where(Document.id == document_id)) is None:
                        raise ValueError("Document not found")
            finally:
                engine.dispose()
        print(f"Processing document {document_id}", file=sys.stderr)
        summary = process_document(str(path), document_id)
    except Exception as exc:
        print(f"Processing could not finish ({type(exc).__name__}); check PDF path, credentials, database and migrations. Document ID: {document_id}", file=sys.stderr)
        return 1
    print(summary.model_dump_json(indent=2))
    return 1 if summary.status == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
