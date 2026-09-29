"""CLI: python -m app.parsing.inspect_chunks sample.pdf."""

import argparse
from collections import Counter
import json
import statistics
import sys

from app.parsing.chunking import chunk_document
from app.parsing.parser import PDFParsingError, parse_pdf_document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect section-aware PDF chunks and estimated token sizes.")
    parser.add_argument("pdf", help="Path to a local PDF")
    parser.add_argument("--examples", type=int, default=3, help="Number of example chunks (default: 3)")
    args = parser.parse_args(argv)
    if args.examples < 0:
        parser.error("--examples must be non-negative")
    try:
        document = parse_pdf_document(args.pdf)
        chunks = chunk_document(document.blocks)
    except (OSError, ValueError, PDFParsingError) as exc:
        print(f"Chunk inspection failed: {exc}", file=sys.stderr)
        return 1
    sizes = [chunk.estimated_token_count for chunk in chunks]
    kinds = Counter(chunk.metadata["kind"] for chunk in chunks)
    # Include a table among examples when present, even if it occurs late in the PDF.
    examples = list(chunks[:args.examples])
    first_table = next((chunk for chunk in chunks if chunk.metadata["kind"] == "table"), None)
    if examples and first_table is not None and not any(c.metadata["kind"] == "table" for c in examples):
        examples[-1] = first_table
    print(json.dumps({
        "total_pages": document.total_pages, "total_blocks": len(document.blocks),
        "total_chunks": len(chunks), "prose_chunks": kinds["prose"], "table_chunks": kinds["table"],
        "token_estimator": "ceil(unicode_characters / 4)",
        "token_distribution": {
            "min": min(sizes, default=0), "max": max(sizes, default=0),
            "mean": round(statistics.mean(sizes), 1) if sizes else 0,
            "median": statistics.median(sizes) if sizes else 0,
            "under_600": sum(size < 600 for size in sizes),
            "600_to_1000": sum(600 <= size <= 1000 for size in sizes),
            "over_1000": sum(size > 1000 for size in sizes),
        },
        "examples": [chunk.model_dump(mode="json") for chunk in examples],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
