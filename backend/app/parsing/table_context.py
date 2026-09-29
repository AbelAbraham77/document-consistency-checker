"""Conservative table context association over provenance-bearing blocks.

Explicit Docling links take priority. Fallbacks require reading-order adjacency,
same-page geometry, column overlap, and role-specific textual evidence.
Source blocks and table text remain separate and unchanged.
"""

from collections import defaultdict
from copy import deepcopy
import re
from typing import Any

from app.parsing.models import DocumentBlock

_TABLE_TITLE = re.compile(r"^\s*table\s+(?:\d+[A-Za-z]?|[IVXLCDM]+)\b", re.IGNORECASE)
_FIGURE_TITLE = re.compile(r"^\s*(?:fig(?:ure)?\.?|chart|exhibit)\s+\w", re.IGNORECASE)
_INTRO = re.compile(
    r"\b(?:following\s+table|table\s+below|this\s+table|"
    r"(?:set\s+(?:out|forth)|summari[sz]ed|presented|shown|listed)\s+below)\b"
    r"|\bfollowing\s*:\s*$"
    r"|\bas\s+follows\s*:\s*$"
    r"|^\s*\(?in\s+(?:thousands|millions|billions)\b"
    r"|^\s*\(?(?:all\s+)?(?:amounts|figures|values)\s+(?:are\s+)?(?:in|expressed\s+in)\b",
    re.IGNORECASE,
)
_NOTE = re.compile(r"^\s*(?:notes?|sources?)\s*[:.]", re.IGNORECASE)
_NOTE_MARKER = re.compile(r"^\s*(\(\d+\)|\([a-z]\)|[*†‡]+)\s+", re.IGNORECASE)


def _pages(block: DocumentBlock) -> list[int]:
    return block.metadata.get("page_numbers", [block.page_number])


def _box(block: DocumentBlock, page: int) -> tuple[float, float, float, float] | None:
    boxes = [entry["bbox"] for entry in block.metadata.get("layout", []) if entry["page_number"] == page]
    if not boxes:
        return None
    return (
        min(box["l"] for box in boxes), min(box["t"] for box in boxes),
        max(box["r"] for box in boxes), max(box["b"] for box in boxes),
    )


def _near(above: DocumentBlock, below: DocumentBlock, *, max_gap: float = 36) -> bool:
    # No speculative association across pages, columns, or omitted content.
    if above.metadata.get("context_segment") != below.metadata.get("context_segment"):
        return False
    if above.metadata.get("source_sha256") != below.metadata.get("source_sha256"):
        return False
    page = max(_pages(above))
    if page != min(_pages(below)):
        return False
    a, b = _box(above, page), _box(below, page)
    if a is None or b is None:
        return False
    width = min(a[2] - a[0], b[2] - b[0])
    overlap = min(a[2], b[2]) - max(a[0], b[0])
    return width > 0 and overlap / width >= 0.5 and -2 <= b[1] - a[3] <= max_gap


def _caption(block: DocumentBlock) -> bool:
    if _FIGURE_TITLE.match(block.text):
        return False
    return block.block_type == "caption" or (
        block.block_type in {"paragraph", "heading"} and bool(_TABLE_TITLE.match(block.text))
    )


def _footnote(block: DocumentBlock, table: DocumentBlock) -> bool:
    marker = _NOTE_MARKER.match(block.text)
    return block.block_type == "footnote" or (
        block.block_type == "paragraph" and (
            bool(_NOTE.match(block.text)) or bool(
                marker and re.search(r"[^\s|]\s*" + re.escape(marker[1]), table.text)
            )
        )
    )


def _snapshot(block: DocumentBlock, reason: str) -> dict[str, Any]:
    return {
        "block_id": block.block_id,
        "block_type": block.block_type,
        "page_number": block.page_number,
        "text": block.text,
        "association": reason,
        "metadata": deepcopy({key: value for key, value in block.metadata.items() if key != "table_context"}),
    }


def attach_table_context(blocks: list[DocumentBlock]) -> None:
    """Attach context snapshots to table metadata in place, without merging text.

    Each table receives `metadata.table_context` with `section_heading`,
    `captions`, `preceding_paragraph`, and `footnotes`. All snapshots retain their
    source block ID, pages, and provenance. Ambiguous shared candidates are omitted.
    """
    by_id = {block.block_id: block for block in blocks}
    tables = [block for block in blocks if block.block_type == "table"]
    # Reserve explicitly linked items before inferring any relationships.
    explicit_owners: dict[str, set[str]] = defaultdict(set)
    for table in tables:
        for ids in table.metadata.get("table_relation_ids", {}).values():
            for block_id in ids:
                explicit_owners[block_id].add(table.block_id)

    def available(candidate: DocumentBlock, table: DocumentBlock) -> bool:
        owners = candidate.metadata.get("context_owner_refs", [])
        return (
            (not owners or set(owners) == {table.metadata.get("context_ref")})
            and (not explicit_owners[candidate.block_id] or explicit_owners[candidate.block_id] == {table.block_id})
            and candidate.metadata.get("source_sha256") == table.metadata.get("source_sha256")
        )

    proposals: dict[str, list[tuple[DocumentBlock, str, str]]] = defaultdict(list)
    contexts = {}
    preceding_heading = None
    for index, table in enumerate(blocks):
        if table.block_type == "heading":
            # A table title remains a heading block but should not replace section context.
            if not _TABLE_TITLE.match(table.text) and not explicit_owners[table.block_id] and not table.metadata.get("context_owner_refs"):
                preceding_heading = table
            continue
        if table.block_type != "table":
            continue
        heading = preceding_heading
        if heading is not None and heading.metadata.get("source_sha256") != table.metadata.get("source_sha256"):
            heading = None
        contexts[table.block_id] = {
            "section_heading": _snapshot(heading, "preceding_heading") if heading else None,
            "captions": [], "preceding_paragraph": None, "footnotes": [],
        }
        table.section_heading = heading.text if heading else None

        def propose(candidate: DocumentBlock, role: str, reason: str) -> None:
            if available(candidate, table):
                proposals[candidate.block_id].append((table, role, reason))

        for role in ("captions", "footnotes"):
            for block_id in table.metadata.get("table_relation_ids", {}).get(role, []):
                candidate = by_id.get(block_id)
                if candidate and candidate.block_type != "table":
                    propose(candidate, role, "docling_reference")

        # Only the immediately preceding paragraph, optionally separated by a title.
        previous = index - 1
        anchor = table
        while previous >= 0:
            candidate = blocks[previous]
            if not available(candidate, table) or not _near(candidate, anchor):
                break
            if _caption(candidate):
                propose(candidate, "captions", "adjacent_layout")
                anchor = candidate
                previous -= 1
                continue
            if candidate.block_type == "paragraph" and _INTRO.search(candidate.text):
                propose(candidate, "preceding_paragraph", "adjacent_layout_and_table_intro")
            break

        # Stop at the first unrelated item, another table, heading, figure, or page.
        following = index + 1
        anchor = table
        saw_note = False
        linked_captions = table.metadata.get("table_relation_ids", {}).get("captions", [])
        while following < len(blocks):
            candidate = blocks[following]
            # Docling may traverse an above-table caption after its parent table.
            # Its explicit link is already attached; it must not hide notes below.
            if candidate.block_id in linked_captions and available(candidate, table):
                if _near(anchor, candidate):
                    anchor = candidate
                following += 1
                continue
            if not available(candidate, table) or not _near(anchor, candidate, max_gap=24 if saw_note else 36):
                break
            if not saw_note and _caption(candidate):
                propose(candidate, "captions", "adjacent_layout")
            elif _footnote(candidate, table):
                propose(candidate, "footnotes", "adjacent_layout")
                saw_note = True
            else:
                break
            anchor = candidate
            following += 1

    # A caption between two plausible tables is left unattached, not assigned by chance.
    for candidate_id, matches in proposals.items():
        explicit = [match for match in matches if match[2] == "docling_reference"]
        selected = explicit or matches
        if len({table.block_id for table, _, _ in selected}) != 1:
            continue
        table, role, reason = selected[0]
        snapshot = _snapshot(by_id[candidate_id], reason)
        if role == "preceding_paragraph":
            contexts[table.block_id][role] = snapshot
        else:
            contexts[table.block_id][role].append(snapshot)

    order = {block.block_id: index for index, block in enumerate(blocks)}
    for table in tables:
        context = contexts[table.block_id]
        for role in ("captions", "footnotes"):
            context[role].sort(key=lambda entry: order[entry["block_id"]])
        table.metadata["table_context"] = context
