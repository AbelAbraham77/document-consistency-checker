"""Re-embed one stored document using the selected provider, then enable retry."""

import argparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database.session import create_database_engine
from app.embeddings.cache import SQLiteEmbeddingCache
from app.embeddings.service import EmbeddingService
from app.embeddings.settings import EmbeddingSettings
from app.embeddings.text import embedding_text, text_hash
from app.jobs.submission import ACTIVE_STAGES
from app.models import Claim, Document
from app.providers.factory import embedding_provider


def reembed_document(session: Session, document_id: UUID, *, provider=None, cache=None) -> int:
    document = session.get(Document, document_id)
    if document is None:
        raise ValueError("Document does not exist")
    if document.status in ACTIVE_STAGES:
        raise ValueError("Wait for current processing to finish before re-embedding")
    settings = EmbeddingSettings()
    provider = provider or embedding_provider(settings)
    cache = cache or SQLiteEmbeddingCache(settings.embedding_cache_path)
    service = EmbeddingService(session, provider, cache, threshold=settings.embedding_similarity_threshold)
    changed = 0
    claim_ids = list(session.scalars(select(Claim.id).where(Claim.document_id == document_id).order_by(Claim.id)))
    for claim_id in claim_ids:
        claim = session.get(Claim, claim_id)
        if (claim.embedding_model != provider.namespace or claim.embedding is None
                or claim.embedding_text_hash != text_hash(embedding_text(claim))):
            service.embed_claim(claim)
            session.commit()  # A failed request leaves earlier successful claims reusable.
            changed += 1
    if changed:
        document.status = "FAILED"
        document.processing_state = {key: value for key, value in (document.processing_state or {}).items()
                                     if key not in {"summary", "candidate_checkpoint", "local_processing_complete", "verification_pending"}}
        session.commit()
    return changed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document_id", type=UUID)
    args = parser.parse_args(argv)
    engine = create_database_engine(Settings())
    if engine is None:
        parser.error("Set DATABASE_URL and apply migrations first")
    try:
        with Session(engine) as session:
            changed = reembed_document(session, args.document_id)
            print(f"Re-embedded {changed} claim(s). Retry document processing to refresh candidates.")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
