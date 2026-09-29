"""Import all persistence models to register their tables with Base.metadata."""

from app.models.records import Claim, Chunk, Contradiction, Document

__all__ = ["Document", "Chunk", "Claim", "Contradiction"]
