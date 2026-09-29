"""Own transactions at stage/item boundaries so a failed LLM call is recoverable."""

from contextlib import contextmanager
import hashlib
import logging
import inspect
import math
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from pydantic import ValidationError

from app.config import Settings
from app.database.repositories import bulk_insert_chunks, get_chunks_by_document, get_claims_by_document
from app.database.session import create_database_engine
from app.database.verification import get_visible_contradictions
from app.embeddings.cache import SQLiteEmbeddingCache
from app.embeddings.provider import EmbeddingError
from app.embeddings.service import EmbeddingService
from app.embeddings.settings import EmbeddingSettings
from app.providers.factory import embedding_provider, text_provider
from app.extraction.local import LocalFactExtractor as default_service
from app.extraction.service import ExtractionError
from app.extraction.settings import ExtractionSettings
from app.matching import generate_structured_candidates
from app.matching.models import CandidatePair
from app.matching.ranking import CascadeSettings, suspicion_score
from app.database.verification import save_verification_result
from app.models import Document, Contradiction
from app.normalization import normalize_stored_claim
from app.parsing import chunk_document, parse_pdf_document
from app.pipeline.models import ProcessingSummary, DocumentAlreadyProcessing
from app.schemas.persistence import ChunkCreate
from app.pipeline.chunks import extract_chunk
from app.verification import candidate_from_claims, VerificationService
from app.verification.settings import VerificationSettings
from app.verification.service import VerificationError
from app.verification.service import rule_based_verifier
from app.llm_retries import PERMANENT_429_CODES

logger = logging.getLogger(__name__)


class ProcessingCancelled(Exception):
    """Raised at a processing boundary after a user cancels a document."""


def _embedding_service(session):
    settings = EmbeddingSettings()
    return EmbeddingService(session, embedding_provider(settings),
        SQLiteEmbeddingCache(settings.embedding_cache_path), threshold=settings.embedding_similarity_threshold)


def _verification_service(session):
    settings = VerificationSettings()
    transport_settings = ExtractionSettings(openai_api_key=settings.openai_api_key,
        extraction_timeout_seconds=settings.verification_timeout_seconds,
        extraction_max_output_tokens=settings.verification_max_output_tokens)
    return VerificationService(session, text_provider(transport_settings, response_name="contradiction_verification",
                                                     model=settings.model or None),
        max_retries=settings.verification_max_retries, max_context_characters=settings.verification_max_context_characters)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for data in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


class DocumentPipeline:
    """A single-worker runner. Owns commits; supply an exclusive, dedicated Session."""
    def __init__(self, session: Session, *, parser=parse_pdf_document, chunker=chunk_document,
                 extractor_factory=default_service, embedding_factory=_embedding_service,
                 verification_factory=_verification_service, page_batch_size: int = 25):
        self.session = session
        self.parser, self.chunker = parser, chunker
        self.extractor_factory = extractor_factory
        self.embedding_factory, self.verification_factory = embedding_factory, verification_factory
        self.page_batch_size = page_batch_size
        self._full_parsed = None

    def _parse_page_batch(self, path: Path, start: int, end: int):
        parameters = inspect.signature(self.parser).parameters
        if "page_start" in parameters:
            return self.parser(str(path), page_start=start, page_end=end)
        if self._full_parsed is None:
            self._full_parsed = self.parser(str(path))
        blocks = [block for block in self._full_parsed.blocks
                  if start <= block.page_number <= end]
        return self._full_parsed.model_copy(update={"blocks": blocks})

    def _refresh_local_pool(self, document, summary):
        claims = get_claims_by_document(self.session, document.id)
        normalized, valid_claims = [], []
        for claim in claims:
            try:
                item = normalize_stored_claim(claim)
                claim.entity_normalized, claim.metric_normalized = item.entity_normalized, item.metric_normalized
                self.session.flush()
                normalized.append(normalize_stored_claim(claim))
                valid_claims.append(claim)
            except Exception as exc:
                self._error(document, summary, "NORMALIZING", claim.id, exc)
        embedding = self.embedding_factory(self.session)
        for claim in valid_claims:
            if claim.embedding is not None:
                continue
            try:
                embedding.embed_claim(claim)
                summary.embeddings_completed += 1
                self.session.commit()
            except Exception as exc:
                summary.embeddings_failed += 1
                self._error(document, summary, "EMBEDDING", claim.id, exc)
        pairs = {self._pair_key(pair): pair for pair in generate_structured_candidates(normalized)}
        for claim in valid_claims:
            for match in embedding.find_similar_claims(claim, document.id):
                pair = self._pair_key(candidate_from_claims(claim, match.claim))
                pairs.setdefault(pair, candidate_from_claims(claim, match.claim))
        for pair in pairs.values():
            result = rule_based_verifier(pair)
            save_verification_result(self.session, document_id=document.id,
                claim_a_id=pair.claim_a.claim_id, claim_b_id=pair.claim_b.claim_id, result=result)
        summary.claims = len(claims)
        summary.verified_contradictions = len(get_visible_contradictions(self.session, document.id))
        summary.candidates_total = len(pairs)
        summary.candidates_verified = len(pairs)
        summary.candidates_pending = 0
        self._progress(document, summary)

    def _stage(self, document, stage):
        self._check_cancelled(document)
        self.current_stage = stage
        document.status = stage
        self.session.commit()
        logger.info("Document processing stage: document=%s stage=%s", document.id, stage)

    def _check_cancelled(self, document):
        self.session.refresh(document, attribute_names=["status"])
        if document.status == "CANCELLED":
            raise ProcessingCancelled()

    def _error(self, document, summary, stage, item_id, exc):
        self.session.rollback()
        # Validation/SDK exceptions can contain source text or credentials. Store safe categories.
        error = {"stage": stage, "item_id": str(item_id), "error_type": type(exc).__name__,
                 "message": f"{stage} failed for this item; retry processing after checking source/configuration."}
        if isinstance(exc, (ExtractionError, VerificationError, EmbeddingError)):
            error["message"] = str(exc)  # These services deliberately sanitize their public errors.
            error.update(category=exc.category, retryable=exc.retryable, attempts=exc.attempts, http_status=exc.http_status)
            if exc.provider:
                error["provider"] = exc.provider
        elif isinstance(exc, ValidationError):
            error["message"] = "Invalid fields: " + ", ".join(
                ".".join(str(part) for part in entry["loc"]) for entry in exc.errors(include_input=False))
        summary.errors.append(error)
        document.processing_state = {**(document.processing_state or {}), "errors": list(summary.errors)}
        self.session.commit()
        logger.warning("Document item failed: document=%s stage=%s item=%s category=%s",
                       document.id, stage, item_id, type(exc).__name__)

    def _progress(self, document, summary):
        """Commit per-document counters after each item, never inside an LLM transaction."""
        document.processing_state = {**(document.processing_state or {}),
            "counts": summary.model_dump(mode="json", exclude={"errors", "status", "document_id"})}
        self.session.commit()

    def process_document(self, pdf_path: str, document_id: UUID) -> ProcessingSummary:
        document = self.session.get(Document, document_id)
        if document is None:
            raise ValueError("Document ID does not exist; create a document record first")
        path = Path(pdf_path).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() != ".pdf":
            raise ValueError("pdf_path must identify an existing PDF file")
        digest = _file_hash(path)
        state = document.processing_state or {}
        if state.get("source_sha256") and state["source_sha256"] != digest:
            raise ValueError("PDF content differs from this document's checkpoint; use a new document ID")
        if document.status == "COMPLETED" and state.get("summary"):
            logger.info("Completed document reused: document=%s", document.id)
            return ProcessingSummary.model_validate(state["summary"])
        summary = ProcessingSummary(document_id=document.id, status="PARSING")
        # storage_path is an opaque durable reference; never replace it with a temporary download.
        document.processing_state = {**state, "source_sha256": digest,
            "errors": state.get("errors", []), "counts": state.get("counts", {})}
        self.session.commit()
        try:
            if (state.get("local_processing_complete") or state.get("verification_pending")) and "candidate_checkpoint" in state:
                summary = ProcessingSummary.model_validate({**state.get("counts", {}),
                    "document_id": document.id, "status": "VERIFYING",
                    "errors": [error for error in state.get("errors", []) if error["stage"] != "VERIFYING"]})
                return self._verify_checkpoint(document, summary)
            self._stage(document, "PARSING")
            if not state.get("chunking_complete"):
                if get_chunks_by_document(self.session, document.id) and not state.get("page_batch_size"):
                    raise ValueError("Existing chunks have no pipeline checkpoint; use a new document ID")
                parsed = self._parse_page_batch(path, 1, self.page_batch_size)
                document.page_count = parsed.total_pages
                self.session.commit()
                self._stage(document, "CHUNKING")
                state = document.processing_state
                total_batches = math.ceil(document.page_count / self.page_batch_size)
                first_batch = int(state.get("last_page_batch", -1)) + 1
                for batch_index in range(first_batch, total_batches):
                    start = batch_index * self.page_batch_size + 1
                    end = min(document.page_count, start + self.page_batch_size - 1)
                    parsed = self._parse_page_batch(path, start, end)
                    parsed_chunks = self.chunker(parsed.blocks)
                    rows = bulk_insert_chunks(self.session, document.id, [ChunkCreate(
                        page_start=item.page_start, page_end=item.page_end, section=item.section, text=item.text,
                    ) for item in parsed_chunks])
                    for row, item in zip(rows, parsed_chunks):
                        row.parsed_data = item.model_dump(mode="json")
                    document.processing_state = {**document.processing_state,
                        "page_batch_size": self.page_batch_size, "total_page_batches": total_batches,
                        "last_page_batch": batch_index}
                    self.session.commit()
                document.processing_state = {**document.processing_state, "chunking_complete": True}
                self.session.commit()
            else:
                self._stage(document, "CHUNKING")
            chunks = get_chunks_by_document(self.session, document.id)
            summary.pages, summary.chunks = document.page_count, len(chunks)
            summary.page_batch_size = self.page_batch_size
            summary.total_page_batches = math.ceil(document.page_count / self.page_batch_size)
            self._stage(document, "EXTRACTING_CLAIMS")
            extractor = None
            consecutive_provider_blocks = 0
            provider_blocked = False
            last_batch = int((document.processing_state or {}).get("last_processed_page_batch", -1))
            total_batches = math.ceil(document.page_count / self.page_batch_size)
            for batch_index in range(last_batch + 1, total_batches):
                batch_start = batch_index * self.page_batch_size + 1
                batch_end = min(document.page_count, batch_start + self.page_batch_size - 1)
                batch_chunks = [chunk for chunk in chunks if batch_start <= chunk.page_start <= batch_end]
                for chunk in batch_chunks:
                    self._check_cancelled(document)
                    if chunk.extraction_status == "COMPLETED":
                        continue
                    chunk_id = chunk.id
                    try:
                        def get_extractor():
                            nonlocal extractor
                            extractor = extractor or self.extractor_factory()
                            return extractor
                        extract_chunk(self.session, document.id, chunk_id, extractor_factory=get_extractor)
                        consecutive_provider_blocks = 0
                    except Exception as exc:
                        self._error(document, summary, "EXTRACTING_CLAIMS", chunk_id, exc)
                        # A lost commit acknowledgement must not undo a durable completion.
                        self.session.refresh(chunk)
                        if chunk.extraction_status != "COMPLETED":
                            chunk.extraction_status = "FAILED"
                            self.session.commit()
                        if isinstance(exc, ExtractionError) and exc.http_status in {400, 401, 403, 429}:
                            consecutive_provider_blocks += 1
                            if (exc.category in PERMANENT_429_CODES
                                    or (exc.http_status == 400 and not exc.retryable)
                                    or exc.category in {"invalid_gemini_api_key", "invalid_gemini_schema", "invalid_gemini_model"}
                                    or exc.http_status in {401, 403} or consecutive_provider_blocks >= 2):
                                provider_blocked = True
                                break
                        else:
                            consecutive_provider_blocks = 0
                summary.claims = len(get_claims_by_document(self.session, document.id))
                summary.pages_processed = batch_end
                if provider_blocked:
                    break
                self._refresh_local_pool(document, summary)
                if all(chunk.extraction_status == "COMPLETED" for chunk in batch_chunks):
                    document.processing_state = {**document.processing_state,
                        "last_processed_page_batch": batch_index,
                        "pages_processed": batch_end}
                self.session.commit()
            summary.claims = len(get_claims_by_document(self.session, document.id))
            logger.info("Pipeline diagnostics: extracted_claims=%d", summary.claims)
            if provider_blocked or (summary.errors and summary.claims == 0):
                summary.status = "FAILED"
                document.processing_state = {**document.processing_state,
                    "summary": summary.model_dump(mode="json"), "counts": summary.model_dump(mode="json"),
                    "errors": summary.errors}
                self._stage(document, "FAILED")
                return summary
            self._stage(document, "NORMALIZING")
            claims = get_claims_by_document(self.session, document.id)
            normalized, valid_claims = [], []
            for claim in claims:
                self._check_cancelled(document)
                claim_id = claim.id
                try:
                    item = normalize_stored_claim(claim)
                    claim.entity_normalized, claim.metric_normalized = item.entity_normalized, item.metric_normalized
                    self.session.flush()
                    # Snapshot persisted normalized names so verifier stale checks remain meaningful.
                    snapshot = normalize_stored_claim(claim)
                    self.session.commit()
                    normalized.append(snapshot)
                    valid_claims.append(claim)
                except Exception as exc:
                    self._error(document, summary, "NORMALIZING", claim_id, exc)
            summary.claims = len(claims)
            logger.info("Pipeline diagnostics: normalized_claims=%d normalization_rejected=%d",
                        len(normalized), len(claims) - len(normalized))
            self._stage(document, "GENERATING_CANDIDATES")
            deterministic = generate_structured_candidates(normalized)
            summary.deterministic_candidates = len(deterministic)
            logger.info("Pipeline diagnostics: structured_candidates=%d", len(deterministic))
            document.processing_state = {**document.processing_state, "counts": summary.model_dump(mode="json")}
            self.session.commit()
            pairs = {self._pair_key(pair): pair for pair in deterministic}
            semantic_keys = set()
            similarities = {}
            embedding = None
            embedded = []
            quota_exhausted = False
            # Complete embedding ingestion first so every query sees all available vectors.
            for claim in valid_claims:
                self._check_cancelled(document)
                claim_id = claim.id
                try:
                    embedding = embedding or self.embedding_factory(self.session)
                    embedding.embed_claim(claim)
                    self.session.commit()
                    embedded.append(claim)
                    summary.embeddings_completed += 1
                except ProcessingCancelled:
                    raise
                except Exception as exc:
                    summary.embeddings_failed += 1
                    self._error(document, summary, "EMBEDDING", claim_id, exc)
                    quota_exhausted = isinstance(exc, EmbeddingError) and exc.category in PERMANENT_429_CODES
                self._progress(document, summary)
                if quota_exhausted:
                    break
            if quota_exhausted:
                summary.status = "FAILED"
                document.processing_state = {**document.processing_state,
                    "summary": summary.model_dump(mode="json"), "counts": summary.model_dump(mode="json"),
                    "errors": summary.errors}
                self._stage(document, "FAILED")
                return summary
            for claim in embedded:
                self._check_cancelled(document)
                claim_id = claim.id
                try:
                    for match in embedding.find_similar_claims(claim, document.id):
                        pair = candidate_from_claims(claim, match.claim)
                        key = self._pair_key(pair)
                        semantic_keys.add(key)
                        similarities[key] = max(similarities.get(key, 0.0), getattr(match, "similarity", 0.0))
                        pairs.setdefault(key, pair)
                except Exception as exc:
                    self._error(document, summary, "SEMANTIC_RETRIEVAL", claim_id, exc)
            summary.semantic_candidates = len(semantic_keys)
            summary.candidates_before_filtering = len(pairs)
            settings = CascadeSettings()
            ranked = sorted(((suspicion_score(pair, similarities.get(key, 0.0)), key, pair)
                             for key, pair in pairs.items()), key=lambda item: (-item[0], str(item[1])))
            checkpoint = {}
            rejected_by_score = 0
            for score, key, pair in ranked:
                if score < settings.candidate_score_threshold:
                    rejected_by_score += 1
                    continue
                candidate_id = f"{key[0]}/{key[1]}"
                checkpoint[candidate_id] = {"pair": pair.model_dump(mode="json"), "score": score,
                                            "status": "VERIFICATION_PENDING"}
            summary.locally_extracted_facts = summary.claims
            summary.candidates_total = summary.candidates_after_filtering = len(checkpoint)
            logger.info(
                "Pipeline diagnostics: semantic_candidates=%d candidates_before_filtering=%d "
                "rejected_by_score=%d candidates_sent_for_verification=%d threshold=%.2f",
                summary.semantic_candidates, summary.candidates_before_filtering,
                rejected_by_score, len(checkpoint), settings.candidate_score_threshold)
            document.processing_state = {**document.processing_state,
                "candidate_checkpoint": checkpoint, "local_processing_complete": not summary.errors}
            self._progress(document, summary)
            return self._verify_checkpoint(document, summary)
        except ProcessingCancelled:
            summary.status = "CANCELLED"
            logger.info("Document processing cancelled: document=%s", document.id)
        except Exception as exc:
            self.session.refresh(document)
            if document.status == "CANCELLED":
                summary.status = "CANCELLED"
                logger.info("Document processing cancelled: document=%s", document.id)
            else:
                stage = getattr(self, "current_stage", "PARSING")
                self._error(document, summary, stage, document_id, exc)
                summary.status = "FAILED"
                document.processing_state = {**document.processing_state, "summary": summary.model_dump(mode="json")}
                self._stage(document, "FAILED")
        logger.info("Processing summary: pages=%d chunks=%d claims=%d deterministic_candidates=%d semantic_candidates=%d verified_contradictions=%d status=%s",
            summary.pages, summary.chunks, summary.claims, summary.deterministic_candidates,
            summary.semantic_candidates, summary.verified_contradictions, summary.status)
        return summary

    def _verify_checkpoint(self, document, summary):
        settings = CascadeSettings()
        self._stage(document, "VERIFYING")
        checkpoint = dict(document.processing_state["candidate_checkpoint"])
        completed = {tuple(sorted((row.claim_a_id, row.claim_b_id))): row for row in self.session.scalars(
            select(Contradiction).where(Contradiction.document_id == document.id,
                Contradiction.status.in_(["verified", "dismissed", "contextual_difference"])))}
        for item in checkpoint.values():
            pair = CandidatePair.model_validate(item["pair"])
            existing = completed.get(self._pair_key(pair))
            if existing is not None and existing.source == "gemini":
                item["status"] = "VERIFIED"
                item["source"] = "gemini"
            else:
                result = rule_based_verifier(pair)
                save_verification_result(self.session, document_id=document.id,
                    claim_a_id=pair.claim_a.claim_id, claim_b_id=pair.claim_b.claim_id, result=result)
                item["status"] = "VERIFIED"
                item["source"] = "rules"
                item["classification"] = result.classification
                item["score"] = item.get("score", 0)
                item["rule_confidence"] = result.confidence
                self.session.commit()
        used = 0
        def progress():
            summary.candidates_verified = sum(item["status"] == "VERIFIED" for item in checkpoint.values())
            summary.candidates_failed = sum(item["status"] == "VERIFICATION_FAILED" for item in checkpoint.values())
            summary.candidates_pending = sum(item["status"] == "VERIFICATION_PENDING" for item in checkpoint.values())
            document.processing_state = {**document.processing_state, "candidate_checkpoint": dict(checkpoint)}
            self._progress(document, summary)
        def on_request():
            nonlocal used
            self._check_cancelled(document)
            if used >= settings.verification_request_budget:
                raise VerificationError("Verification request budget reached; resume later.", category="request_budget")
            used += 1
            summary.gemini_requests += 1
            progress()  # Count attempts durably, including malformed outputs and quota responses.
        def on_result(candidate_id, result):
            self._check_cancelled(document)
            pair = CandidatePair.model_validate(checkpoint[candidate_id]["pair"])
            if result is not None:
                save_verification_result(self.session, document_id=document.id,
                    claim_a_id=pair.claim_a.claim_id, claim_b_id=pair.claim_b.claim_id, result=result)
                logger.info("Pipeline diagnostics: verification_classification=%s candidate=%s",
                            result.classification, candidate_id)
            checkpoint[candidate_id] = {**checkpoint[candidate_id],
                "status": "VERIFIED" if result is not None else "VERIFICATION_FAILED"}
            progress()
            if result is not None and result.classification == "INSUFFICIENT_CONTEXT":
                self._error(document, summary, "VERIFYING", candidate_id,
                    VerificationError("This comparison needs source review: insufficient source context.",
                                      category="insufficient_context"))
        progress()
        pending = sorted(
            (key for key, item in checkpoint.items()
             if item.get("source") == "rules" and item.get("classification") == "UNCERTAIN"),
            key=lambda key: checkpoint[key].get("score", 0), reverse=True)[:10]
        logger.info("Pipeline diagnostics: rule_uncertain=%d", len(pending))
        verifier = None
        if pending:
            self._check_cancelled(document)
            batch = {key: CandidatePair.model_validate(checkpoint[key]["pair"]) for key in pending}
            try:
                verifier = self.verification_factory(self.session)
                verifier.verify_batch(batch, on_result=on_result, on_request=on_request)
            except ProcessingCancelled:
                raise
            except Exception as exc:
                self._error(document, summary, "VERIFYING", pending[0], exc)
                summary.errors[-1]["message"] = "AI check unavailable, rule-based results shown."
                document.processing_state = {**document.processing_state, "errors": summary.errors}
                self.session.commit()
                logger.warning("AI verification unavailable; retaining rule-based verdicts: %s", exc)
            progress()
        progress()
        summary.verified_contradictions = len(get_visible_contradictions(self.session, document.id))
        logger.info("Pipeline diagnostics: saved_inconsistencies=%d", summary.verified_contradictions)
        summary.candidates_pending = 0
        summary.status = "COMPLETED"
        document.processing_state = {**document.processing_state, "summary": summary.model_dump(mode="json"),
                                    "verification_pending": bool(summary.candidates_pending), "counts": summary.model_dump(mode="json"), "errors": summary.errors}
        self._stage(document, summary.status)
        return summary

    @staticmethod
    def _pair_key(pair):
        return tuple(sorted((pair.claim_a.claim_id, pair.claim_b.claim_id)))


@contextmanager
def _document_lock(connection, document_id):
    key = int.from_bytes(hashlib.sha256(str(document_id).encode()).digest()[:8], "big", signed=True)
    acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
    connection.commit()
    if not acquired:
        raise DocumentAlreadyProcessing("This document is already being processed")
    try:
        yield
    finally:
        connection.rollback()
        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
        connection.commit()


def process_document(pdf_path: str, document_id: UUID, *, settings: Settings | None = None) -> ProcessingSummary:
    document_id = UUID(str(document_id))
    engine = create_database_engine(settings if settings is not None else Settings())
    if engine is None:
        raise ValueError("Set DATABASE_URL and apply migrations before processing")
    try:
        # Session-level advisory lock requires a pinned connection (direct/session pooler).
        with engine.connect() as connection, _document_lock(connection, document_id):
            with Session(connection, expire_on_commit=False) as session:
                return DocumentPipeline(session).process_document(pdf_path, document_id)
    finally:
        engine.dispose()
