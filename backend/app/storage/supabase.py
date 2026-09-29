"""Backend-only Supabase REST access; no public or signed download URLs."""

from contextlib import contextmanager
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from .base import StorageError


class SupabaseDocumentStorage:
    def __init__(self, url: str, service_role_key: str, bucket: str, *, timeout: float = 60,
                 max_bytes: int = 50 * 1024 * 1024, transport=None):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
            raise StorageError("SUPABASE_URL must be an HTTPS project origin")
        if not service_role_key.strip() or not re.fullmatch(r"[A-Za-z0-9_-]+", bucket):
            raise StorageError("Configure a Supabase service-role key and private bucket")
        self.url, self.bucket = url.rstrip("/"), bucket
        self._key = service_role_key
        self.timeout, self.max_bytes, self.transport = timeout, max_bytes, transport

    @contextmanager
    def _client(self):
        try:
            with httpx.Client(base_url=f"{self.url}/storage/v1/", timeout=self.timeout,
                              follow_redirects=False, transport=self.transport,
                              headers={"apikey": self._key, "Authorization": f"Bearer {self._key}"}) as client:
                response = client.get(f"bucket/{self.bucket}")
                self._check(response)
                bucket = response.json()
                if not isinstance(bucket, dict) or bucket.get("public") is not False:
                    raise StorageError("Document storage bucket must be private")
                yield client
        except (httpx.HTTPError, ValueError, OSError):
            raise StorageError("Supabase document storage is unavailable") from None

    @staticmethod
    def _check(response):
        if not response.is_success:
            raise StorageError("Supabase storage request failed; check bucket and backend credentials")

    def _key_from_reference(self, reference):
        prefix = f"supabase://{self.bucket}/"
        key = reference.removeprefix(prefix)
        if not reference.startswith(prefix) or not re.fullmatch(r"[a-f0-9-]{36}\.pdf", key):
            raise StorageError("Invalid Supabase storage reference")
        return key

    def save(self, source: Path) -> str:
        key = f"{uuid4()}.pdf"
        with self._client() as client:
            size = source.stat().st_size
            if size > self.max_bytes:
                raise StorageError("PDF exceeds storage size limit")
            with source.open("rb") as stream:
                response = client.post(f"object/{self.bucket}/{key}", content=iter(lambda: stream.read(1024 * 1024), b""),
                                       headers={"Content-Type": "application/pdf", "Content-Length": str(size), "x-upsert": "false"})
            self._check(response)
        return f"supabase://{self.bucket}/{key}"

    @contextmanager
    def retrieve(self, reference: str):
        key = self._key_from_reference(reference)
        with TemporaryDirectory(prefix="document-source-") as directory:
            path = Path(directory) / "source.pdf"
            with self._client() as client:
                with client.stream("GET", f"object/authenticated/{self.bucket}/{key}") as response:
                    self._check(response)
                    size = 0
                    with path.open("wb") as target:
                        for data in response.iter_bytes():
                            size += len(data)
                            if size > self.max_bytes:
                                raise StorageError("Stored PDF exceeds storage size limit")
                            target.write(data)
            yield path

    def delete(self, reference: str) -> None:
        key = self._key_from_reference(reference)
        with self._client() as client:
            self._check(client.request("DELETE", f"object/{self.bucket}", json={"prefixes": [key]}))
