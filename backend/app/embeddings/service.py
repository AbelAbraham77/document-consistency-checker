"""Embed persisted claims and retrieve same-document semantic candidates."""

from contextlib import contextmanager
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database.session import create_database_engine, create_session_factory
from app.database.vectors import SimilarClaim, find_similar_claims_by_vector, save_claim_embedding
from app.embeddings.cache import EmbeddingCache, SQLiteEmbeddingCache
from app.embeddings.provider import EmbeddingProvider
from app.providers.factory import embedding_provider
from app.embeddings.settings import EmbeddingSettings
from app.embeddings.text import embedding_text, text_hash
from app.embeddings.vectors import validate_vector
from app.models import Claim

logger = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(self, session: Session, provider: EmbeddingProvider, cache: EmbeddingCache, *, threshold: float = 0.8):
        if not -1 <= threshold <= 1:
            raise ValueError("Similarity threshold must be between -1 and 1")
        self.session, self.provider, self.cache, self.threshold = session, provider, cache, threshold

    def _stored(self, claim: Claim) -> Claim:
        if not isinstance(claim, Claim) or claim.id is None or claim.document_id is None:
            raise ValueError("Expected a persisted ORM Claim with document and claim IDs")
        stored = self.session.scalar(select(Claim).where(Claim.id == claim.id, Claim.document_id == claim.document_id))
        if stored is None:
            raise ValueError("Claim does not belong to the specified document")
        return stored

    def embed_claim(self, claim: Claim) -> list[float]:
        stored = self._stored(claim)
        text = embedding_text(stored)
        digest = text_hash(text)
        if (stored.embedding is not None and stored.embedding_model == self.provider.namespace
                and stored.embedding_text_hash == digest):
            logger.info("Embedding reused from claim storage")
            return validate_vector([float(value) for value in stored.embedding])
        vector = self.cache.get_or_create(self.provider.namespace, digest, lambda: self.provider.embed(text))
        save_claim_embedding(self.session, stored, vector, namespace=self.provider.namespace, text_hash=digest)
        logger.info("Claim embedding stored")
        return vector

    def find_similar_claims(self, claim: Claim, document_id: UUID, top_k: int = 10) -> list[SimilarClaim]:
        if not isinstance(document_id, UUID) or claim.document_id != document_id:
            raise ValueError("Query claim must belong to the requested document")
        if type(top_k) is not int or not 1 <= top_k <= 1000:
            raise ValueError("top_k must be an integer between 1 and 1000")
        if self.session.get_bind().dialect.name != "postgresql":
            raise ValueError("Semantic retrieval requires PostgreSQL with pgvector")
        stored = self._stored(claim)
        vector = self.embed_claim(stored)
        results = find_similar_claims_by_vector(self.session, document_id=document_id,
                    claim_id=stored.id, vector=vector, namespace=self.provider.namespace,
                    top_k=top_k, threshold=self.threshold)
        # Claims edited without re-embedding must not return stale semantic evidence.
        return [result for result in results
                if result.claim.embedding_text_hash == text_hash(embedding_text(result.claim))]


@contextmanager
def _default_service():
    settings = EmbeddingSettings()
    engine = create_database_engine(Settings())
    if engine is None:
        raise ValueError("Set DATABASE_URL before storing or retrieving embeddings")
    try:
        with create_session_factory(engine).begin() as session:
            yield EmbeddingService(session, embedding_provider(settings),
                SQLiteEmbeddingCache(settings.embedding_cache_path), threshold=settings.embedding_similarity_threshold)
    finally:
        engine.dispose()


def embed_claim(claim: Claim) -> list[float]:
    with _default_service() as service:
        return service.embed_claim(claim)


def find_similar_claims(claim: Claim, document_id: UUID, top_k: int = 10) -> list[SimilarClaim]:
    with _default_service() as service:
        return service.find_similar_claims(claim, document_id, top_k)
