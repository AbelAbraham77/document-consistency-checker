"""Run with python -m app.extraction.extract_chunk chunk.json."""

import argparse
import logging
from pathlib import Path
import sys

from app.extraction.service import ExtractionError, default_service
from app.parsing.models import DocumentChunk


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract claims from one DocumentChunk JSON file")
    parser.add_argument("chunk", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        chunk = DocumentChunk.model_validate_json(args.chunk.read_text(encoding="utf-8-sig"))
        response = default_service().extract(chunk)
    except (OSError, ValueError, ExtractionError) as exc:
        # Validation errors can contain source text; do not print their input values.
        message = str(exc) if isinstance(exc, ExtractionError) else (
            "Check the chunk JSON/provenance and backend provider API key (GEMINI_API_KEY by default)."
        )
        print(f"Extraction failed: {message}", file=sys.stderr)
        return 1
    print(response.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
