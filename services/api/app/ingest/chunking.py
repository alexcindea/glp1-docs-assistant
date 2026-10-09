"""ParsedDocument -> chunks, each knowing which page and box every character came from.

Placeholder until checkpoint 3: one chunk per block. The interface (ChunkDraft
with spans that cover the chunk text end to end) is what storage, the worker
and later citation mapping are built against.
"""

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.ingest.models import BlockKind, ParsedDocument, Region, check_coverage

CHUNKER_VERSION = "blocks-v0"


def approx_tokens(text: str) -> int:
    """Rough token count (words x 1.3) until the embedding model's tokenizer is chosen."""
    return round(len(text.split()) * 1.3)


class ChunkDraft(BaseModel):
    kind: Literal["text", "table"]
    text: str = Field(min_length=1)
    section_path: list[str]
    token_count: int = Field(ge=0)
    block_indices: list[int] = Field(min_length=1)
    table_block: int | None = None  # the table's block index, for table chunks
    # Ranges of `text` and where they sit: same rules as a block's regions.
    spans: list[Region] = Field(min_length=1)

    @model_validator(mode="after")
    def _spans_cover_text(self) -> Self:
        check_coverage(self.text, self.spans, whole=self.kind == "table")
        return self


def chunk_document(parsed: ParsedDocument) -> list[ChunkDraft]:
    chunks = []
    for block in parsed.blocks:
        if block.kind == BlockKind.INDEX:
            continue
        is_table = block.kind == BlockKind.TABLE
        chunks.append(
            ChunkDraft(
                kind="table" if is_table else "text",
                text=block.text,
                section_path=block.section_path,
                token_count=approx_tokens(block.text),
                block_indices=[block.index],
                table_block=block.index if is_table else None,
                spans=block.regions,
            )
        )
    return chunks
