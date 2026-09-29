"""Documents, chunks, claims, and contradiction candidates.

Revision ID: 0001_initial
Revises: None
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def uuid_pk():
    return sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def created_at():
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def document_id():
    return sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)


def upgrade():
    # Keep an existing installation (including Supabase's extensions schema).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table("documents",
        uuid_pk(), sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="uploaded"),
        created_at(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("page_count >= 0", name="page_count_nonnegative"),
    )
    op.create_index("ix_documents_status", "documents", ["status"])
    op.create_table("chunks",
        uuid_pk(), document_id(),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("section", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False), created_at(),
        sa.CheckConstraint("page_start >= 1 AND page_end >= page_start", name="valid_page_range"),
        sa.UniqueConstraint("document_id", "id", name="uq_chunks_document_id_id"),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    op.create_index("ix_chunks_document_pages", "chunks", ["document_id", "page_start", "page_end"])
    op.create_index("ix_chunks_content_hash", "chunks", ["content_hash"])
    op.create_table("claims",
        uuid_pk(), document_id(), sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("entity_raw", sa.Text(), nullable=False),
        sa.Column("entity_normalized", sa.String(255), nullable=True),
        sa.Column("metric_raw", sa.Text(), nullable=False),
        sa.Column("metric_normalized", sa.String(255), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_numeric", sa.Numeric(), nullable=True),
        sa.Column("unit", sa.String(64), nullable=True),
        sa.Column("period", sa.String(128), nullable=True),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("qualifier", sa.Text(), nullable=True),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("source_page", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("embedding", Vector(), nullable=True), created_at(),
        sa.UniqueConstraint("document_id", "id", name="uq_claims_document_id_id"),
        sa.ForeignKeyConstraint(["document_id", "chunk_id"], ["chunks.document_id", "chunks.id"], name="fk_claims_document_chunk", ondelete="CASCADE"),
        sa.CheckConstraint("source_page >= 1", name="source_page_positive"),
        sa.CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"),
    )
    for column in ("document_id", "chunk_id", "entity_normalized", "metric_normalized", "period"):
        op.create_index(f"ix_claims_{column}", "claims", [column])
    op.create_index("ix_claims_candidate_keys", "claims", ["document_id", "entity_normalized", "metric_normalized", "period"])
    op.create_table("contradictions",
        uuid_pk(), document_id(),
        sa.Column("claim_a_id", sa.Uuid(), nullable=False),
        sa.Column("claim_b_id", sa.Uuid(), nullable=False),
        sa.Column("classification", sa.String(64), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="candidate"), created_at(),
        sa.ForeignKeyConstraint(["document_id", "claim_a_id"], ["claims.document_id", "claims.id"], name="fk_contradictions_claim_a", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id", "claim_b_id"], ["claims.document_id", "claims.id"], name="fk_contradictions_claim_b", ondelete="CASCADE"),
        sa.CheckConstraint("claim_a_id < claim_b_id", name="canonical_distinct_pair"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        sa.UniqueConstraint("document_id", "claim_a_id", "claim_b_id", name="uq_contradictions_pair"),
    )
    for column in ("document_id", "claim_a_id", "claim_b_id", "status"):
        op.create_index(f"ix_contradictions_{column}", "contradictions", [column])
    op.execute("""
        CREATE FUNCTION document_consistency_set_updated_at() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at = clock_timestamp();
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER documents_updated_at BEFORE UPDATE ON documents
        FOR EACH ROW EXECUTE FUNCTION document_consistency_set_updated_at()
    """)


def downgrade():
    op.drop_table("contradictions")
    op.drop_table("claims")
    op.drop_table("chunks")
    op.drop_table("documents")
    op.execute("DROP FUNCTION document_consistency_set_updated_at()")
    # The vector extension may be shared with other applications; do not drop it.
