"""Reject invalid, zero and out-of-range vectors before pgvector storage."""

import math

from app.database.vector_types import EMBEDDING_DIMENSIONS


def validate_vector(values) -> list[float]:
    if not isinstance(values, (list, tuple)) or len(values) != EMBEDDING_DIMENSIONS:
        raise ValueError(f"Expected {EMBEDDING_DIMENSIONS} embedding dimensions")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        raise ValueError("Embedding coordinates must be numbers")
    vector = [float(value) for value in values]
    if any(not math.isfinite(value) or abs(value) > 3.402823466e38 for value in vector):
        raise ValueError("Embedding coordinates must be finite float32 values")
    if not any(abs(value) >= 1.175494351e-38 for value in vector):
        raise ValueError("A cosine embedding must be nonzero")
    return vector
