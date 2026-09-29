"""Database backstop for replayed claim-extraction writes."""

from alembic import op
import sqlalchemy as sa

revision = "0005_chunk_idempotency"
down_revision = "0004_document_ownership"
branch_labels = None
depends_on = None


def upgrade():
    # Legacy/manual claims have no extraction key. Completed chunk checkpoints
    # already prevent their replay; do not rewrite or delete existing claims.
    op.add_column("claims", sa.Column("extraction_key", sa.String(64), nullable=True))
    op.create_unique_constraint("uq_claims_chunk_extraction_key", "claims", ["chunk_id", "extraction_key"])


def downgrade():
    op.drop_constraint("uq_claims_chunk_extraction_key", "claims", type_="unique")
    op.drop_column("claims", "extraction_key")
