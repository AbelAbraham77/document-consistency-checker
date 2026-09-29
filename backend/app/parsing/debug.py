"""Human-readable table inspection without altering extracted blocks."""

from collections.abc import Iterable
import json
import sys
from typing import TextIO

from app.parsing.models import DocumentBlock


def debug_table(table: DocumentBlock, *, file: TextIO | None = None) -> None:
    """Print one table, its attached context, association reasons, and provenance."""
    if table.block_type != "table":
        raise ValueError("debug_table expects a table block")
    output = file if file is not None else sys.stdout

    def emit(label, entry):
        if entry is None:
            print(f"\n{label}: (none attached)", file=output)
            return
        metadata = entry["metadata"]
        pages = metadata.get("page_numbers", [entry["page_number"]])
        print(f"\n{label} | pages {pages} | block {entry['block_id']}", file=output)
        print(f"Association: {entry.get('association', 'table content')}", file=output)
        print(f"Source: {metadata.get('source_path', '(unknown)')}", file=output)
        print(entry["text"], file=output)
        print("Provenance: " + json.dumps(metadata.get("provenance", []), ensure_ascii=True), file=output)

    print("\n" + "=" * 72, file=output)
    context = table.metadata.get("table_context", {})
    emit("SECTION", context.get("section_heading"))
    for caption in context.get("captions", []) or [None]:
        emit("CAPTION / TITLE", caption)
    emit("PRECEDING EXPLANATION", context.get("preceding_paragraph"))
    emit("TABLE", table.model_dump(mode="json"))
    for note in context.get("footnotes", []) or [None]:
        emit("FOOTNOTE", note)


def debug_tables(blocks: Iterable[DocumentBlock], *, file: TextIO | None = None) -> None:
    """Print all table blocks in document order."""
    found = False
    for block in blocks:
        if block.block_type == "table":
            debug_table(block, file=file)
            found = True
    if not found:
        print("No tables found.", file=file if file is not None else sys.stdout)
