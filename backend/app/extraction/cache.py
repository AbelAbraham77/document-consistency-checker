"""Content cache contracts and independent memory/SQLite implementations."""

from contextlib import contextmanager
import hashlib
from pathlib import Path
import sqlite3
from threading import RLock
from typing import ContextManager, Iterator, Protocol

from pydantic import BaseModel, ConfigDict

from app.extraction.grounding import evidence_segments, validate_grounding
from app.extraction.schemas import ClaimExtractionResponse
from app.parsing.models import DocumentChunk


def normalized_text_map(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Normalize line endings and outer whitespace, retaining original offsets.

    Preserve case, Unicode, internal whitespace and table layout: they may carry meaning.
    """
    characters, offsets = [], []
    index = 0
    while index < len(text):
        end = index + (2 if text[index:index + 2] == "\r\n" else 1)
        characters.append("\n" if text[index] == "\r" else text[index])
        offsets.append((index, end))
        index = end
    result = "".join(characters)
    start = len(result) - len(result.lstrip())
    end = len(result.rstrip())
    return result[start:end], offsets[start:end]


def content_hash(text: str) -> str:
    normalized, _ = normalized_text_map(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class CacheReuseError(ValueError):
    """Cached content cannot safely be bound to the supplied chunk context."""


class CachedExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    normalized_text: str
    section: str | None
    response: ClaimExtractionResponse
    # Quote offsets in normalized chunk text, independent of document/page IDs.
    anchors: list[tuple[int, int]]


def _segment_ranges(chunk: DocumentChunk):
    cursor = 0
    for segment in evidence_segments(chunk):
        start = chunk.text.find(segment["text"], cursor)
        if start < 0:
            raise CacheReuseError("Cannot locate source evidence in chunk")
        end = start + len(segment["text"])
        yield start, end, segment["page"]
        cursor = end


def cache_entry(chunk: DocumentChunk, response: ClaimExtractionResponse) -> CachedExtraction:
    normalized, offsets = normalized_text_map(chunk.text)
    ranges = list(_segment_ranges(chunk))
    anchors = []
    for claim in response.claims:
        found = None
        for start, end, page in ranges:
            position = chunk.text.find(claim.claim_text, start, end)
            if page == claim.source_page and position >= 0:
                finish = position + len(claim.claim_text)
                indices = [i for i, (a, b) in enumerate(offsets) if a < finish and b > position]
                if indices:
                    found = (indices[0], indices[-1] + 1)
                    break
        if found is None:
            raise CacheReuseError("Cannot anchor extracted quote")
        anchors.append(found)
    return CachedExtraction(normalized_text=normalized, section=chunk.section,
                            response=response.model_copy(deep=True), anchors=anchors)


def reuse_entry(entry: CachedExtraction, chunk: DocumentChunk) -> ClaimExtractionResponse:
    normalized, offsets = normalized_text_map(chunk.text)
    if entry.normalized_text != normalized or entry.section != chunk.section:
        raise CacheReuseError("Cached extraction has different content or section context")
    response = entry.response.model_copy(deep=True)
    if len(entry.anchors) != len(response.claims):
        raise CacheReuseError("Invalid cached quote anchors")
    ranges = list(_segment_ranges(chunk))
    for claim, (start, end) in zip(response.claims, entry.anchors):
        if not 0 <= start < end <= len(offsets):
            raise CacheReuseError("Invalid cached quote offsets")
        original_start, original_end = offsets[start][0], offsets[end - 1][1]
        pages = {page for a, b, page in ranges if a <= original_start and original_end <= b}
        if len(pages) != 1 or None in pages:
            raise CacheReuseError("Cached quote has ambiguous page provenance in this chunk")
        quote = chunk.text[original_start:original_end]
        if normalized_text_map(claim.claim_text)[0] != normalized_text_map(quote)[0]:
            raise CacheReuseError("Cached quote does not match content")
        # Rebind value text as well when its line endings differ.
        value, _ = normalized_text_map(claim.value_text)
        normalized_quote, quote_offsets = normalized_text_map(quote)
        position = normalized_quote.find(value)
        if position < 0 or not value:
            raise CacheReuseError("Cached value does not match quote")
        claim.value_text = quote[quote_offsets[position][0]:quote_offsets[position + len(value) - 1][1]]
        claim.claim_text = quote
        claim.source_page = pages.pop()
    validate_grounding(response, evidence_segments(chunk))
    return response


class CacheSlot(Protocol):
    def get(self) -> CachedExtraction | None: ...
    def put(self, entry: CachedExtraction) -> None: ...


class ExtractionCache(Protocol):
    def locked(self, key: str) -> ContextManager[CacheSlot]:
        """Hold an exclusive lock through lookup, extraction and successful write."""
        ...


class MemoryExtractionCache:
    """Thread-safe process-local cache, injectable for tests or short-lived use."""
    def __init__(self):
        self._entries: dict[str, CachedExtraction] = {}
        self._lock = RLock()

    @contextmanager
    def locked(self, key: str) -> Iterator[CacheSlot]:
        with self._lock:
            entries = self._entries

            class Slot:
                def get(self):
                    entry = entries.get(key)
                    return entry.model_copy(deep=True) if entry is not None else None

                def put(self, entry):
                    entries[key] = entry.model_copy(deep=True)

            yield Slot()


class SQLiteExtractionCache:
    """Durable cache without application DB/ORM dependencies.

    A write transaction serializes misses across processes, including the provider
    call. This deliberately trades throughput for duplicate-call prevention.
    """
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def locked(self, key: str) -> Iterator[CacheSlot]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=120)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE IF NOT EXISTS extraction_cache_v1 (content_hash TEXT PRIMARY KEY, payload TEXT NOT NULL)")

            class Slot:
                def get(self):
                    row = connection.execute("SELECT payload FROM extraction_cache_v1 WHERE content_hash = ?", (key,)).fetchone()
                    return CachedExtraction.model_validate_json(row[0]) if row else None

                def put(self, entry):
                    connection.execute("INSERT OR REPLACE INTO extraction_cache_v1 VALUES (?, ?)",
                                       (key, entry.model_dump_json()))

            yield Slot()
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
