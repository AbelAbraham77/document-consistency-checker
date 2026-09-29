"""Inspect a PDF: python -m app.parsing.cli path/to/document.pdf."""

import argparse
from collections import Counter
import json
import logging
import sys

from app.parsing.parser import PDFParsingError, parse_pdf_document
from app.parsing.debug import debug_tables


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect PDF blocks and source provenance.")
    parser.add_argument("pdf", help="Path to a local PDF file")
    parser.add_argument("--debug-tables", action="store_true", help="Print tables with attached context and provenance")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    try:
        document = parse_pdf_document(args.pdf)
    except (OSError, ValueError, PDFParsingError) as exc:
        print(f"Parsing failed: {exc}", file=sys.stderr)
        return 1

    if args.debug_tables:
        debug_tables(document.blocks)
        return 0

    counts = Counter(block.block_type for block in document.blocks)
    # JSON escaping makes output safe on Windows terminals with legacy encodings.
    print(json.dumps({
        "total_pages": document.total_pages,
        "total_blocks": len(document.blocks),
        "paragraphs": counts["paragraph"],
        "headings": counts["heading"],
        "tables": counts["table"],
        "ocr_pages": document.ocr_pages,
        "first_10_blocks": [block.model_dump(mode="json") for block in document.blocks[:10]],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
