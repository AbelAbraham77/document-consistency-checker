"""Persistent, model-isolated text-hash cache, independent of the application DB."""

from collections.abc import Callable
import json
from pathlib import Path
import sqlite3
from typing import Protocol

from app.embeddings.vectors import validate_vector


class EmbeddingCache(Protocol):
    def get_or_create(self, namespace: str, text_hash: str,
                      create: Callable[[], list[float]]) -> list[float]: ...


class SQLiteEmbeddingCache:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def get_or_create(self, namespace, text_hash, create):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=120)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE IF NOT EXISTS embeddings_v1 (namespace TEXT, text_hash TEXT, vector TEXT NOT NULL, PRIMARY KEY (namespace, text_hash))")
            row = connection.execute("SELECT vector FROM embeddings_v1 WHERE namespace=? AND text_hash=?",
                                     (namespace, text_hash)).fetchone()
            vector = validate_vector(json.loads(row[0])) if row else validate_vector(create())
            if row is None:
                connection.execute("INSERT INTO embeddings_v1 VALUES (?, ?, ?)",
                                   (namespace, text_hash, json.dumps(vector, allow_nan=False)))
            connection.commit()
            return vector
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
