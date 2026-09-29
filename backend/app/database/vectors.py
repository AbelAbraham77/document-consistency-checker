"""pgvector persistence/query operations; callers own transactions."""

from dataclasses import dataclass
import math
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.embeddings.vectors import validate_vector
from app.models import Claim


@dataclass(frozen=True)
class SimilarClaim:
    claim: Claim
    similarity: float
    preliminary_classification: str = "semantic_candidate"


def save_claim_embedding(session: Session, claim: Claim, vector: list[float], *, namespace: str, text_hash: str):
    claim.embedding = validate_vector(vector)
    claim.embedding_model = namespace
    claim.embedding_text_hash = text_hash
    session.flush()


def similarity_statement(*, document_id: UUID, claim_id: UUID, vector: list[float], namespace: str,
                         top_k: int = 10, threshold: float = 0.8):
    if type(top_k) is not int or not 1 <= top_k <= 1000:
        raise ValueError("top_k must be an integer between 1 and 1000")
    if not math.isfinite(threshold) or not -1 <= threshold <= 1:
        raise ValueError("Similarity threshold must be between -1 and 1")
    if not isinstance(document_id, UUID) or not isinstance(claim_id, UUID):
        raise ValueError("Document and claim UUIDs are required")
    distance = Claim.embedding.cosine_distance(validate_vector(vector))
    return (select(Claim, (1 - distance).label("similarity"))
            .where(Claim.document_id == document_id, Claim.id != claim_id,
                   Claim.embedding.is_not(None), Claim.embedding_model == namespace,
                   Claim.embedding_text_hash.is_not(None), distance <= 1 - threshold)
            .order_by(distance).limit(top_k))


def find_similar_claims_by_vector(session: Session, **kwargs) -> list[SimilarClaim]:
    if session.get_bind().dialect.name != "postgresql":
        raise ValueError("Semantic retrieval requires PostgreSQL with pgvector")
    statement = similarity_statement(**kwargs)
    return [SimilarClaim(claim=row[0], similarity=float(row[1])) for row in session.execute(statement)]
