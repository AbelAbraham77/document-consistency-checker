"""Embedding storage and semantic candidate retrieval are initialized only on use."""

__all__ = ["EmbeddingService", "embed_claim", "find_similar_claims"]


def __getattr__(name):
    if name in __all__:
        from app.embeddings import service
        return getattr(service, name)
    raise AttributeError(name)
