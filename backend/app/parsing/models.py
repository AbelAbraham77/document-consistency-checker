"""Public parsing contracts with one-based PDF page numbers."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

BlockType = Literal["paragraph", "heading", "table", "caption", "footnote"]


class DocumentBlock(BaseModel):
    """An extracted item; multi-page items list every page in metadata.

    `page_number` is the first physical PDF page, not a printed page label.
    Text is Docling's extracted text (Markdown for tables), not normalized claims.
    """

    model_config = ConfigDict(extra="forbid")

    block_id: str = Field(min_length=1)
    page_number: int = Field(gt=0, strict=True)
    block_type: BlockType
    text: str = Field(min_length=1)
    section_heading: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ParsedDocument(BaseModel):
    """Parsing report used by the CLI, including empty PDF pages."""

    source_path: str
    total_pages: int = Field(ge=1)
    blocks: list[DocumentBlock] = Field(default_factory=list)
    ocr_pages: list[int] = Field(default_factory=list)


class DocumentChunk(BaseModel):
    """Section-aware content with source block spans and full provenance metadata."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1)
    page_start: int = Field(gt=0, strict=True)
    page_end: int = Field(gt=0, strict=True)
    section: str | None = None
    block_types: list[BlockType] = Field(min_length=1)
    text: str = Field(min_length=1)
    source_block_ids: list[str] = Field(min_length=1)
    estimated_token_count: int = Field(gt=0)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_page_range(self) -> "DocumentChunk":
        if self.page_end < self.page_start:
            raise ValueError("page_end must not precede page_start")
        return self
