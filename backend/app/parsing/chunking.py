"""Deterministic semantic chunking by sections, paragraphs, and atomic tables."""

from __future__ import annotations

from copy import deepcopy
from bisect import bisect_left, bisect_right
import hashlib
import json
import math
import re

from app.parsing.models import DocumentBlock, DocumentChunk


def estimate_tokens(text: str) -> int:
    """Cheap, model-independent estimate: one token per four Unicode characters."""
    return math.ceil(len(text) / 4)


def _page_numbers(block: DocumentBlock) -> list[int]:
    pages = {block.page_number}
    pages.update(block.metadata.get("page_numbers", []))
    pages.update(item["page_no"] for item in block.metadata.get("provenance", []))
    return sorted(pages)


def _join(blocks: list[DocumentBlock]) -> tuple[str, list[tuple[DocumentBlock, int, int]]]:
    parts, locations = [], []
    offset = 0
    for block in blocks:
        if parts:
            offset += 2
        parts.append(block.text)
        locations.append((block, offset, offset + len(block.text)))
        offset += len(block.text)
    return "\n\n".join(parts), locations


def _chunk(
    text: str,
    locations: list[tuple[DocumentBlock, int, int]],
    start: int,
    end: int,
    section: str | None,
    kind: str,
    overlap_chars: int,
) -> DocumentChunk:
    # Trim separators without losing the original block offsets.
    original_start = start
    if kind == "prose":
        start += len(text[start:end]) - len(text[start:end].lstrip())
        end -= len(text[start:end]) - len(text[start:end].rstrip())
    content = text[start:end]
    sources, spans, pages, types = {}, [], set(), []
    for block, block_start, block_end in locations:
        low, high = max(start, block_start), min(end, block_end)
        if low >= high:
            continue
        spans.append({
            "block_id": block.block_id,
            "char_start": low - block_start, "char_end": high - block_start,
            "chunk_char_start": low - start, "chunk_char_end": high - start,
        })
        sources[block.block_id] = {
            "page_number": block.page_number, "block_type": block.block_type,
            "section_heading": block.section_heading, "metadata": deepcopy(block.metadata),
        }
        pages.update(_page_numbers(block))
        if block.block_type not in types:
            types.append(block.block_type)
    identity = json.dumps({"section": section, "spans": spans, "text": content, "pages": sorted(pages)}, sort_keys=True)
    return DocumentChunk(
        chunk_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        page_start=min(pages), page_end=max(pages), section=section,
        block_types=types, text=content, source_block_ids=list(sources),
        estimated_token_count=estimate_tokens(content),
        metadata={
            "kind": kind, "page_numbers": sorted(pages), "source_blocks": sources,
            "source_spans": spans, "token_estimator": "ceil(unicode_characters / 4)",
            "page_precision": "source_block",
            "overlap_characters": max(0, overlap_chars - (start - original_start)),
        },
    )


def _prose_chunks(blocks, section, target, minimum, maximum, overlap):
    text, locations = _join(blocks)
    if not text.strip():
        return []
    # Prefer paragraph ends, then sentence ends. Neither is a one-sentence policy.
    paragraph_ends = [end for _, _, end in locations]
    paragraph_starts = [begin for _, begin, _ in locations]
    sentence_ends = [match.end() for match in re.finditer(r"[.!?][\"')\]]*(?=\s)", text)]
    word_ends = [match.start() for match in re.finditer(r"\s+", text)]
    result = []
    start = previous_end = 0
    while start < len(text):
        remaining = len(text) - start
        if remaining <= maximum:
            end = len(text)
        else:
            # Balance the remaining chunks so a long section does not end in a tiny tail.
            count = max(2, math.ceil((remaining - overlap) / (maximum - overlap)), round((remaining - overlap) / (target - overlap)))
            ideal = start + math.ceil((remaining + (count - 1) * overlap) / count)
            lower = start + min(minimum, (remaining + overlap) // 2)
            upper = start + min(maximum, remaining - min(minimum, (remaining + overlap) // 2) + overlap)
            end = min(start + maximum, ideal)
            for boundaries in (paragraph_ends, sentence_ends, word_ends):
                low = bisect_left(boundaries, lower)
                high = bisect_right(boundaries, upper)
                if low < high:
                    nearest = bisect_left(boundaries, ideal, low, high)
                    eligible = [boundaries[index] for index in {max(low, nearest - 1), min(high - 1, nearest)}]
                    end = min(eligible, key=lambda point: (abs(point - ideal), point))
                    break
        relevant = locations[bisect_right(paragraph_ends, start):bisect_left(paragraph_starts, end)]
        if text[start:end].strip():
            result.append(_chunk(text, relevant, start, end, section, "prose", max(0, previous_end - start)))
        if end == len(text):
            break
        previous_end = end
        if overlap:
            overlap_start = max(start + 1, end - overlap)
            boundary = re.search(r"\s+", text[overlap_start:end])
            start = overlap_start + boundary.end() if boundary else end
        else:
            start = end
    return result


def _context_blocks(table: DocumentBlock, by_id: dict[str, DocumentBlock]):
    context = table.metadata.get("table_context", {})
    entries = [context.get("section_heading"), context.get("preceding_paragraph"), *context.get("captions", [])]
    before, after = [], []
    for destination, snapshots in ((before, entries), (after, context.get("footnotes", []))):
        for snapshot in snapshots:
            if snapshot is None:
                continue
            block = by_id.get(snapshot["block_id"])
            if block is None:
                block = DocumentBlock(
                    block_id=snapshot["block_id"], page_number=snapshot["page_number"],
                    block_type=snapshot["block_type"], text=snapshot["text"],
                    metadata=deepcopy(snapshot.get("metadata", {})),
                )
            destination.append(block)
    return before, after


def chunk_document(
    blocks: list[DocumentBlock], *, target_tokens: int = 800,
    min_tokens: int = 600, max_tokens: int = 1000, overlap_tokens: int = 80,
) -> list[DocumentChunk]:
    """Group section prose and emit one unsplit chunk per table with its context.

    Short sections remain short. Table chunks may exceed max_tokens. Overlap is
    limited to neighboring prose chunks in the same uninterrupted section.
    Input blocks are not mutated. All original page metadata survives in each
    chunk's `metadata.source_blocks`; split blocks retain their full page range.
    """
    values = (target_tokens, min_tokens, max_tokens, overlap_tokens)
    if any(type(value) is not int for value in values) or not (0 <= overlap_tokens < min_tokens <= target_tokens <= max_tokens):
        raise ValueError("Require integers: 0 <= overlap_tokens < min_tokens <= target_tokens <= max_tokens")
    by_id = {block.block_id: block for block in blocks}
    if len(by_id) != len(blocks):
        raise ValueError("Input block IDs must be unique")
    table_parts = {}
    claimed = set()
    for block in blocks:
        if block.block_type == "table":
            before, after = _context_blocks(block, by_id)
            table_parts[block.block_id] = (before, after)
            context = block.metadata.get("table_context", {})
            section_id = (context.get("section_heading") or {}).get("block_id")
            claimed.update(item.block_id for item in before + after if item.block_id != section_id)

    result, pending = [], []
    section = None
    heading = None
    source = None

    def flush():
        if pending:
            result.extend(_prose_chunks(pending, section, target_tokens * 4, min_tokens * 4, max_tokens * 4, overlap_tokens * 4))
            pending.clear()

    for block in blocks:
        if not block.text.strip():
            continue
        block_source = block.metadata.get("source_sha256", block.metadata.get("source_path"))
        if source != block_source:
            flush()
            section = heading = None
            source = block_source
        if block.block_id in claimed:
            continue
        if block.block_type == "table":
            table_section = block.section_heading or section
            before, after = table_parts[block.block_id]
            prelude = []
            if pending and all(item.block_type == "heading" for item in pending) and table_section == section:
                prelude = list(pending)
                pending.clear()
            else:
                flush()
            if not any(item.block_type == "heading" for item in before) and heading and heading.text == table_section:
                prelude.append(heading)
            combined, seen = [], set()
            for item in prelude + before + [block] + after:
                if item.block_id not in seen:
                    combined.append(item)
                    seen.add(item.block_id)
            text, locations = _join(combined)
            result.append(_chunk(text, locations, 0, len(text), table_section, "table", 0))
            continue
        if block.block_type == "heading":
            if any(item.block_type != "heading" for item in pending):
                flush()
            section, heading = block.text, block
        elif block.section_heading is not None and block.section_heading != section:
            flush()
            section, heading = block.section_heading, None
        pending.append(block)
    flush()
    return result
