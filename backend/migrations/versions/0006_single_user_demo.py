"""Remove document ownership and the associated database access policies."""

from alembic import op
import sqlalchemy as sa

revision = "0006_single_user_demo"
down_revision = "0005_chunk_idempotency"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("contradictions", "claims", "chunks", "documents"):
        op.execute(f"DROP POLICY IF EXISTS {table}_owner_access ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_documents_owner_id", table_name="documents")
    op.drop_column("documents", "owner_id")


def downgrade():
    # A demo database has no user identities. A synthetic UUID keeps restored
    # policies closed to real users if the migration is rolled back.
    op.add_column("documents", sa.Column("owner_id", sa.Uuid(), nullable=True))
    op.execute("UPDATE documents SET owner_id = '00000000-0000-0000-0000-000000000001'::uuid")
    op.alter_column("documents", "owner_id", nullable=False)
    op.create_index("ix_documents_owner_id", "documents", ["owner_id"])
    identity = "(NULLIF(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')"
    for table in ("documents", "chunks", "claims", "contradictions"):
        condition = f"owner_id::text = {identity}" if table == "documents" else (
            f"EXISTS (SELECT 1 FROM documents WHERE documents.id = {table}.document_id "
            f"AND documents.owner_id::text = {identity})")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_owner_access ON {table} FOR ALL "
                   f"USING ({condition}) WITH CHECK ({condition})")
