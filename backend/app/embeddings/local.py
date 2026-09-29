"""CPU embeddings; no remote embedding endpoint or API credentials."""

from functools import lru_cache
from pathlib import Path
from app.database.vector_types import EMBEDDING_DIMENSIONS
from app.embeddings.text import TEXT_VERSION
from app.embeddings.vectors import validate_vector


@lru_cache(maxsize=2)
def _model(name):
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(name, device="cpu", trust_remote_code=False,
        cache_folder=str(Path(__file__).resolve().parents[2] / ".model-cache" / "sentence-transformers"))


class LocalEmbeddingProvider:
    provider_name = "local"

    def __init__(self, settings):
        self.model_name = settings.local_embedding_model
        self.namespace = f"sentence-transformers:{self.model_name}:padded-{EMBEDDING_DIMENSIONS}:{TEXT_VERSION}"

    def embed(self, text):
        vector = _model(self.model_name).encode(text, normalize_embeddings=True).tolist()
        if len(vector) > EMBEDDING_DIMENSIONS:
            raise ValueError("Local model exceeds stored vector dimensions")
        # Zero padding preserves cosine similarity and existing pgvector schema.
        return validate_vector(vector + [0.0] * (EMBEDDING_DIMENSIONS - len(vector)))
