"""Storage contracts. Retrieval leases a local file only for the context lifetime."""

from pathlib import Path
from typing import ContextManager, Protocol


class StorageError(RuntimeError):
    """Safe public error: never include credentials or upstream response bodies."""


class DocumentStorage(Protocol):
    def save(self, source: Path) -> str: ...
    def retrieve(self, reference: str) -> ContextManager[Path]: ...
    def delete(self, reference: str) -> None: ...
