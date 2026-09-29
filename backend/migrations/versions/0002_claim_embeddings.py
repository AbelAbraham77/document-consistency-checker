"""Dimensioned claim embeddings and optional cosine HNSW index.

Revision ID: 0002_claim_embeddings
Revises: 0001_initial
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = "0002_claim_embeddings"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    # PostgreSQL refuses incompatible existing vectors; never silently truncate/delete them.
    op.alter_column("claims", "embedding", type_=Vector(1536), existing_type=Vector(),
                    postgresql_using="embedding::vector(1536)")
    op.add_column("claims", sa.Column("embedding_model", sa.String(255), nullable=True))
    op.add_column("claims", sa.Column("embedding_text_hash", sa.String(64), nullable=True))
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_am WHERE amname = 'hnsw') THEN
            CREATE INDEX ix_claims_embedding_hnsw ON claims USING hnsw (embedding vector_cosine_ops);
          ELSE
            RAISE NOTICE 'HNSW unavailable; claim retrieval will use exact vector search';
          END IF;
        END $$;
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_claims_embedding_hnsw")
    op.drop_column("claims", "embedding_text_hash")
    op.drop_column("claims", "embedding_model")
    op.alter_column("claims", "embedding", type_=Vector(), existing_type=Vector(1536),
                    postgresql_using="embedding::vector")
