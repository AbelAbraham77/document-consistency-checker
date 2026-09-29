"""Persist source provenance and resumable pipeline progress.

Revision ID: 0003_processing_progress
Revises: 0002_claim_embeddings
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_processing_progress"
down_revision = "0002_claim_embeddings"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("processing_state", sa.JSON(), nullable=True))
    op.add_column("chunks", sa.Column("parsed_data", sa.JSON(), nullable=True))
    op.add_column("chunks", sa.Column("extraction_status", sa.String(32), nullable=False, server_default="PENDING"))


def downgrade():
    op.drop_column("chunks", "extraction_status")
    op.drop_column("chunks", "parsed_data")
    op.drop_column("documents", "processing_state")
