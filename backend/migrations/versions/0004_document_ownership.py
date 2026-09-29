"""Required ownership and RLS protection for Supabase's direct database API."""

from alembic import op
import sqlalchemy as sa

revision = "0004_document_ownership"
down_revision = "0003_processing_progress"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("owner_id", sa.Uuid(), nullable=True))
    # Revision 0006 removes ownership. Legacy rows may pass through this
    # historical revision without an Auth user while upgrading to head.
    op.create_index("ix_documents_owner_id", "documents", ["owner_id"])
    identity = "(NULLIF(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')"
    for table in ("documents", "chunks", "claims", "contradictions"):
        condition = f"owner_id::text = {identity}" if table == "documents" else (
            f"EXISTS (SELECT 1 FROM documents WHERE documents.id = {table}.document_id AND documents.owner_id::text = {identity})")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_owner_access ON {table} FOR ALL USING ({condition}) WITH CHECK ({condition})")


def downgrade():
    for table in ("contradictions", "claims", "chunks", "documents"):
        op.execute(f"DROP POLICY {table}_owner_access ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_documents_owner_id", table_name="documents")
    op.drop_column("documents", "owner_id")
