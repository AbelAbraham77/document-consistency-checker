"""Development document upload, processing, progress and result endpoints."""

from pathlib import Path
from tempfile import TemporaryDirectory
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_session
from app.jobs.submission import enqueue_document, JobConflict, JobDispatchError, ACTIVE_STAGES
from app.database.verification import get_visible_contradictions
from app.models import Claim, Chunk, Contradiction, Document
from app.storage import StorageError
from app.schemas.documents import (
    ClaimPage, ClaimRead, ClaimWithContext, ContradictionDetail, ContradictionRead,
    DocumentRead, ProcessResponse, ProgressCounts, ProgressResponse, SourceContext, UploadResponse,
)

router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)


def _document(session, document_id):
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(404, "Document not found")
    return document


def _enqueue(request, session, document):
    try:
        enqueue_document(session, document, request.app.state.job_executor)
    except JobConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except JobDispatchError as exc:
        raise HTTPException(503, {"message": str(exc), "document_id": str(document.id)}) from None


@router.post("", response_model=UploadResponse, status_code=201)
def upload_document(request: Request, file: UploadFile = File(...), session: Session = Depends(get_session)):
    name = (file.filename or "").replace("\\", "/").split("/")[-1]
    if not name or Path(name).suffix.lower() != ".pdf":
        raise HTTPException(415, "Upload a file with a .pdf extension")
    if file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(415, "Expected a PDF upload")
    settings = request.app.state.settings
    temporary = TemporaryDirectory(prefix="document-upload-")
    destination = Path(temporary.name) / "source.pdf"
    storage = request.app.state.document_storage
    reference = None
    committed = False
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with destination.open("xb") as target:
            while data := file.file.read(1024 * 1024):
                size += len(data)
                if size > settings.max_upload_bytes:
                    raise HTTPException(413, "PDF exceeds MAX_UPLOAD_BYTES")
                target.write(data)
        with destination.open("rb") as saved:
            if b"%PDF-" not in saved.read(1024):
                raise HTTPException(400, "File is not a valid PDF")
        import pypdfium2 as pdfium
        try:
            with pdfium.PdfDocument(destination) as pdf:
                pages = len(pdf)
                if pages < 1:
                    raise ValueError("Empty PDF")
                # Intake checks only: full page parsing happens in the document worker.
        except Exception:
            raise HTTPException(400, "PDF is corrupt, encrypted, or has no readable pages") from None
        reference = storage.save(destination)
        document = Document(filename=name, storage_path=reference, page_count=pages, status="UPLOADED")
        session.add(document)
        session.commit()
        committed = True
        _enqueue(request, session, document)
        return UploadResponse(document_id=document.id, filename=name, page_count=pages, status="QUEUED")
    except (OSError, StorageError):
        raise HTTPException(503, "Document storage is unavailable; check backend storage configuration") from None
    finally:
        if not committed and reference is not None:
            try:
                storage.delete(reference)
            except (OSError, StorageError):
                logger.warning("Could not clean up an unsuccessful upload")
        temporary.cleanup()
        file.file.close()


@router.post("/{document_id}/process", response_model=ProcessResponse, status_code=202)
def start_processing(document_id: UUID, request: Request, session: Session = Depends(get_session)):
    document = _document(session, document_id)
    _enqueue(request, session, document)
    return ProcessResponse(document_id=document_id)


@router.get("/{document_id}", response_model=DocumentRead)
def document_metadata(document_id: UUID, session: Session = Depends(get_session)):
    return _document(session, document_id)


@router.post("/{document_id}/cancel", status_code=202)
def cancel_processing(document_id: UUID, session: Session = Depends(get_session)):
    document = session.scalar(select(Document).where(Document.id == document_id).with_for_update())
    if document is None:
        raise HTTPException(404, "Document not found")
    if document.status not in ACTIVE_STAGES:
        raise HTTPException(409, "This document is no longer processing")
    document.status = "CANCELLED"
    session.commit()
    return {"document_id": document_id, "status": "CANCELLED"}


@router.get("/{document_id}/progress", response_model=ProgressResponse, response_model_exclude_none=True)
def document_progress(document_id: UUID, session: Session = Depends(get_session)):
    document = _document(session, document_id)
    state = document.processing_state or {}
    checkpoint = state.get("counts", state.get("summary", {}))
    chunk_counts = dict(session.execute(select(Chunk.extraction_status, func.count()).where(
        Chunk.document_id == document_id).group_by(Chunk.extraction_status)).all())
    batch_progress = {
        key: state[key] for key in ("pages_processed", "page_batch_size", "total_page_batches")
        if key in state
    }
    counts = ProgressCounts(pages=document.page_count, **batch_progress, chunks=sum(chunk_counts.values()),
        chunks_completed=chunk_counts.get("COMPLETED", 0), chunks_failed=chunk_counts.get("FAILED", 0),
        claims=session.scalar(select(func.count()).select_from(Claim).where(Claim.document_id == document_id)),
        deterministic_candidates=checkpoint.get("deterministic_candidates", 0),
        semantic_candidates=checkpoint.get("semantic_candidates", 0),
        embeddings_completed=checkpoint.get("embeddings_completed", 0),
        embeddings_failed=checkpoint.get("embeddings_failed", 0),
        locally_extracted_facts=checkpoint.get("locally_extracted_facts", 0),
        candidates_before_filtering=checkpoint.get("candidates_before_filtering", 0),
        candidates_after_filtering=checkpoint.get("candidates_after_filtering", 0),
        gemini_requests=checkpoint.get("gemini_requests", 0),
        candidates_pending=checkpoint.get("candidates_pending", 0),
        candidates_total=checkpoint.get("candidates_total", 0),
        candidates_verified=checkpoint.get("candidates_verified", 0),
        candidates_failed=checkpoint.get("candidates_failed", 0),
        verified_contradictions=session.scalar(select(func.count()).select_from(Contradiction).where(
            Contradiction.document_id == document_id, Contradiction.status == "verified",
            Contradiction.classification == "CONTRADICTION")))
    return ProgressResponse(document_id=document.id, status=document.status, stage=document.status,
                            counts=counts, updated_at=document.updated_at, errors=state.get("errors", []))


@router.get("/{document_id}/claims", response_model=ClaimPage)
def document_claims(document_id: UUID, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                    session: Session = Depends(get_session)):
    _document(session, document_id)
    total = session.scalar(select(func.count()).select_from(Claim).where(Claim.document_id == document_id))
    claims = session.scalars(select(Claim).where(Claim.document_id == document_id)
                            .order_by(Claim.source_page, Claim.created_at, Claim.id).offset(offset).limit(limit))
    return ClaimPage(items=[ClaimRead.model_validate(claim) for claim in claims], total=total, offset=offset, limit=limit)


@router.get("/{document_id}/contradictions", response_model=list[ContradictionRead])
def document_contradictions(document_id: UUID, session: Session = Depends(get_session)):
    _document(session, document_id)
    return get_visible_contradictions(session, document_id)


@router.get("/{document_id}/contradictions/{contradiction_id}", response_model=ContradictionDetail)
def contradiction_detail(document_id: UUID, contradiction_id: UUID, session: Session = Depends(get_session)):
    _document(session, document_id)
    record = session.scalar(select(Contradiction).where(Contradiction.id == contradiction_id,
        Contradiction.document_id == document_id, Contradiction.status == "verified",
        Contradiction.classification == "CONTRADICTION"))
    if record is None:
        raise HTTPException(404, "Verified contradiction not found")
    def with_context(claim_id):
        claim = session.scalar(select(Claim).where(Claim.id == claim_id, Claim.document_id == document_id))
        chunk = None if claim is None else session.scalar(select(Chunk).where(
            Chunk.id == claim.chunk_id, Chunk.document_id == document_id))
        if claim is None or chunk is None:
            raise HTTPException(409, "Original claim context is unavailable")
        return ClaimWithContext(**ClaimRead.model_validate(claim).model_dump(), context=SourceContext.model_validate(chunk))
    return ContradictionDetail(**ContradictionRead.model_validate(record).model_dump(),
                               claim_a=with_context(record.claim_a_id), claim_b=with_context(record.claim_b_id))
