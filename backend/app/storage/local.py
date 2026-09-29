"""Local development storage with confined references and exclusive writes."""

from contextlib import contextmanager
from pathlib import Path
import shutil
from uuid import uuid4

from .base import StorageError


class LocalDocumentStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, reference: str) -> Path:
        path = Path(reference)
        path = (path if path.is_absolute() else self.root / path).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise StorageError("Invalid local storage reference")
        return path

    def save(self, source: Path) -> str:
        path = self.root / f"{uuid4()}.pdf"
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as incoming, path.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            return str(path)
        except OSError:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise StorageError("Local document storage is unavailable") from None

    @contextmanager
    def retrieve(self, reference: str):
        path = self._path(reference)
        if not path.is_file():
            raise StorageError("Stored PDF is unavailable")
        yield path

    def delete(self, reference: str) -> None:
        try:
            self._path(reference).unlink(missing_ok=True)
        except OSError:
            raise StorageError("Could not delete stored PDF") from None
