"""Conservative quote/page checks using chunk source spans, without guessing pages."""

from decimal import Decimal
import re

from app.extraction.schemas import ClaimExtractionResponse
from app.parsing.models import DocumentChunk


def evidence_segments(chunk: DocumentChunk) -> list[dict]:
    if chunk.page_start == chunk.page_end:
        return [{"text": chunk.text, "page": chunk.page_start}]
    spans = chunk.metadata.get("source_spans", [])
    blocks = chunk.metadata.get("source_blocks", {})
    if not isinstance(spans, list) or not isinstance(blocks, dict):
        raise ValueError("Invalid chunk provenance")
    segments = []
    for span in spans:
        if not isinstance(span, dict):
            raise ValueError("Invalid chunk source span")
        start, end = span.get("chunk_char_start"), span.get("chunk_char_end")
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(chunk.text):
            raise ValueError("Invalid chunk source offsets")
        block_id = span.get("block_id")
        if not isinstance(block_id, str):
            raise ValueError("Invalid source block identifier")
        block = blocks.get(block_id, {})
        if not isinstance(block, dict) or not isinstance(block.get("metadata", {}), dict):
            raise ValueError("Invalid chunk source block")
        page = block.get("page_number")
        metadata = block.get("metadata", {})
        pages = metadata.get("page_numbers", [page])
        known = isinstance(pages, list) and pages == [page]
        provenance_items = metadata.get("provenance", [])
        if not isinstance(provenance_items, list):
            raise ValueError("Invalid block provenance")
        for provenance in provenance_items:
            if not isinstance(provenance, dict) or provenance.get("page_no") != page:
                known = False
        if type(page) is not int or not chunk.page_start <= page <= chunk.page_end:
            known = False
        segments.append({"text": chunk.text[start:end], "page": page if known else None})
    if not segments or not any(item["page"] is not None for item in segments):
        raise ValueError("Chunk needs unambiguous source spans to extract page-grounded claims")
    return segments


def validate_grounding(response: ClaimExtractionResponse, segments: list[dict]) -> None:
    for claim in response.claims:
        if claim.value_text not in claim.claim_text:
            raise ValueError("Claim value is not quoted in its source text")
        if claim.value_numeric is not None:
            # Only decimal literals can be checked deterministically. Preserve scale in unit.
            # Number words and ambiguous/localized formats should use a null numeric value.
            tokens = re.findall(r"(?<![\w.])[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\w.])", claim.value_text)
            numbers = [Decimal(token.replace(",", "")) for token in tokens]
            if claim.value_text.strip().startswith("(") and claim.value_text.strip().endswith(")"):
                numbers = [-abs(number) for number in numbers]
            if claim.value_numeric not in numbers:
                raise ValueError("Numeric value is not supported by the quoted value")
        if not any(
            segment["page"] == claim.source_page and claim.claim_text in segment["text"]
            for segment in segments
        ):
            raise ValueError("Claim quote and page do not match source evidence")
