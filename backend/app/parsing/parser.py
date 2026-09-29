"""Local Docling PDF parsing; no API, database, or LLM dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
import hashlib
from itertools import groupby
import logging
from pathlib import Path
from typing import Any

from app.parsing.models import BlockType, DocumentBlock, ParsedDocument
from app.parsing.table_context import attach_table_context

logger = logging.getLogger(__name__)


class PDFParsingError(RuntimeError):
    """The PDF could not be converted completely with reliable provenance."""


class ParsingDependencyError(PDFParsingError):
    """The optional parsing dependencies are missing."""


_INSTALL_HELP = "Install parsing dependencies: python -m pip install -r requirements-parsing.txt"
_LABELS: dict[str, BlockType] = {
    "text": "paragraph",
    "paragraph": "paragraph",
    "list_item": "paragraph",
    "code": "paragraph",
    "formula": "paragraph",
    "title": "heading",
    "section_header": "heading",
    "table": "table",
    "caption": "caption",
    "footnote": "footnote",
}


@dataclass(frozen=True)
class _PDFInfo:
    total_pages: int
    ocr_pages: frozenset[int]


def _inspect_pdf(path: Path, *, page_start: int = 1, page_end: int | None = None) -> _PDFInfo:
    """Count all pages and identify image pages without an embedded text layer."""
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as pdfium_raw
    except ImportError as exc:
        raise ParsingDependencyError(_INSTALL_HELP) from exc

    try:
        with pdfium.PdfDocument(path) as pdf:
            total = len(pdf)
            if total == 0:
                raise PDFParsingError("The PDF contains no pages.")
            ocr_pages = set()
            first = max(1, page_start)
            last = min(total, page_end or total)
            for index in range(first - 1, last):
                with closing(pdf[index]) as page:
                    with closing(page.get_textpage()) as text_page:
                        has_text = bool(text_page.get_text_bounded().strip())
                    if not has_text and any(
                        page.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_IMAGE])
                    ):
                        ocr_pages.add(index + 1)
            return _PDFInfo(total, frozenset(ocr_pages))
    except PDFParsingError:
        raise
    except Exception as exc:
        raise PDFParsingError(f"Cannot read PDF {path.name}: {exc}") from exc


def _make_converter(*, do_ocr: bool) -> Any:
    # Lazy imports keep the health API and block model usable without Docling.
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as exc:
        raise ParsingDependencyError(_INSTALL_HELP) from exc

    options = PdfPipelineOptions(
        do_ocr=do_ocr,
        do_table_structure=True,
        enable_remote_services=False,
        allow_external_plugins=False,
        do_picture_description=False,
        do_picture_classification=False,
        do_code_enrichment=False,
        do_formula_enrichment=False,
    )
    if do_ocr:
        options.ocr_options = RapidOcrOptions(backend="onnxruntime")
    return DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
    )


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _document_blocks(
    document: Any,
    *,
    source_path: Path,
    source_hash: str,
    total_pages: int,
    do_ocr: bool,
    section_heading: str | None,
) -> tuple[list[DocumentBlock], str | None]:
    """Translate Docling items without flattening tables or discarding provenance."""
    from docling_core.types.doc import ContentLayer, DocItemLabel
    from docling_core.transforms.serializer.markdown import MarkdownDocSerializer, MarkdownParams

    blocks = []
    seen = set()
    scope = f"{source_hash}:{min(document.pages)}-{max(document.pages)}"
    owners: dict[str, set[str]] = {}
    relations = {}
    for floating in [*document.tables, *document.pictures]:
        relations[floating.self_ref] = {
            "captions": [ref.cref for ref in floating.captions],
            "footnotes": [ref.cref for ref in floating.footnotes],
        }
        for refs in relations[floating.self_ref].values():
            for ref in refs:
                owners.setdefault(ref, set()).add(f"{scope}:{floating.self_ref}")
    # Export the grid alone; caption/note text is attached with its own provenance.
    serializer = MarkdownDocSerializer(
        doc=document,
        params=MarkdownParams(labels=set(DocItemLabel) - {DocItemLabel.CAPTION, DocItemLabel.FOOTNOTE}),
    )
    segment = 0
    for item, _ in document.iterate_items(
        traverse_pictures=True,
        included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE},
    ):
        label = _value(getattr(item, "label", ""))
        block_type = _LABELS.get(label)
        if block_type is None:
            # Do not bridge an omitted picture/other object when inferring adjacency.
            if label and label not in {"page_header", "page_footer"}:
                segment += 1
            continue
        text = (
            serializer.serialize(item=item).text
            if block_type == "table"
            else item.text
        )
        if not text.strip():
            continue
        provenance = [entry.model_dump(mode="json") for entry in item.prov]
        pages = sorted({entry["page_no"] for entry in provenance})
        if not pages or any(page < 1 or page > total_pages for page in pages):
            raise PDFParsingError(f"Missing or invalid page provenance for {item.self_ref}.")

        identity = f"{source_hash}:{item.self_ref}:{pages}"
        if identity in seen:
            continue
        seen.add(identity)
        if block_type == "heading":
            section_heading = text

        metadata = {
            "source_path": str(source_path),
            "source_sha256": source_hash,
            "docling_ref": item.self_ref,
            "docling_label": label,
            "content_layer": _value(item.content_layer),
            "page_numbers": pages,
            "provenance": provenance,
            "ocr_enabled": do_ocr,
            "context_ref": f"{scope}:{item.self_ref}",
            "context_segment": f"{scope}:{segment}",
            "context_owner_refs": sorted(owners.get(item.self_ref, set())),
            "layout": [
                {
                    "page_number": entry.page_no,
                    "bbox": entry.bbox.to_top_left_origin(
                        page_height=document.pages[entry.page_no].size.height
                    ).model_dump(mode="json"),
                }
                for entry in item.prov
            ],
        }
        ancestor = item.parent
        while ancestor is not None:
            parent = ancestor.resolve(document)
            if _value(getattr(parent, "label", "")) in {"table", "picture"}:
                owner = f"{scope}:{parent.self_ref}"
                if owner not in metadata["context_owner_refs"]:
                    metadata["context_owner_refs"].append(owner)
                break
            ancestor = parent.parent
        if block_type == "table":
            # Preserve row/column indices, header flags, merged-cell spans and bboxes.
            metadata["table"] = item.data.model_dump(mode="json")
            metadata["text_format"] = "markdown"
        else:
            metadata["original_text"] = getattr(item, "orig", text)
        if label == "section_header":
            metadata["heading_level"] = item.level

        blocks.append(
            DocumentBlock(
                block_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                page_number=pages[0],
                block_type=block_type,
                text=text,
                section_heading=section_heading,
                metadata=metadata,
            )
        )
    by_ref = {block.metadata["docling_ref"]: block.block_id for block in blocks}
    for block in blocks:
        if block.block_type == "table":
            block.metadata["table_relation_ids"] = {
                role: [by_ref[ref] for ref in refs if ref in by_ref]
                for role, refs in relations[block.metadata["docling_ref"]].items()
            }
    return blocks, section_heading


def parse_pdf_document(path: str, *, page_start: int = 1, page_end: int | None = None) -> ParsedDocument:
    """Parse a local PDF and retain page totals even for blank pages.

    OCR is restricted to pages with images and no extractable text. When a
    page window is supplied, only that window is converted while total_pages
    still reports the complete PDF size. Consecutive pages with the same OCR
    policy are converted together. Partial conversions are errors, so a failed
    page cannot silently disappear from the result.
    """
    source = Path(path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"PDF not found: {source}")
    if not source.is_file():
        raise ValueError(f"Expected a PDF file: {source}")
    if source.suffix.lower() != ".pdf":
        raise ValueError("Expected a file with a .pdf extension.")
    with source.open("rb") as stream:
        if b"%PDF-" not in stream.read(1024):
            raise PDFParsingError("The file does not have a PDF header.")
        stream.seek(0)
        source_hash = hashlib.file_digest(stream, "sha256").hexdigest()

    info = _inspect_pdf(source, page_start=page_start, page_end=page_end)
    converters = {}
    blocks = []
    section_heading = None
    for do_ocr, page_group in groupby(
        range(max(1, page_start), min(info.total_pages, page_end or info.total_pages) + 1),
        key=lambda page: page in info.ocr_pages
    ):
        pages = list(page_group)
        try:
            if do_ocr not in converters:
                converters[do_ocr] = _make_converter(do_ocr=do_ocr)
            logger.info("Parsing pages %s-%s (OCR=%s)", pages[0], pages[-1], do_ocr)
            result = converters[do_ocr].convert(
                source, page_range=(pages[0], pages[-1]), raises_on_error=True
            )
            if _value(result.status) != "success":
                raise PDFParsingError(
                    f"Docling conversion was not complete: {_value(result.status)} "
                    f"on pages {pages[0]}-{pages[-1]}."
                )
            if set(result.document.pages) != set(pages):
                raise PDFParsingError("Docling did not preserve the requested PDF page numbers.")
            extracted, section_heading = _document_blocks(
                result.document,
                source_path=source,
                source_hash=source_hash,
                total_pages=info.total_pages,
                do_ocr=do_ocr,
                section_heading=section_heading,
            )
            blocks.extend(extracted)
        except PDFParsingError:
            raise
        except Exception as exc:
            raise PDFParsingError(
                f"Failed to parse pages {pages[0]}-{pages[-1]} of {source.name}: {exc}"
            ) from exc

    attach_table_context(blocks)
    return ParsedDocument(
        source_path=str(source),
        total_pages=info.total_pages,
        blocks=blocks,
        ocr_pages=sorted(info.ocr_pages),
    )


def parse_pdf(path: str) -> list[DocumentBlock]:
    """Return layout-aware PDF blocks in reading order with source provenance."""
    return parse_pdf_document(path).blocks
