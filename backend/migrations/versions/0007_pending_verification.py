"""Allow the pending-verification terminal document status."""
from alembic import op
import sqlalchemy as sa

revision = "0007_pending_verification"
down_revision = "0006_single_user_demo"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("documents", "status", type_=sa.String(64), existing_type=sa.String(32))


def downgrade():
    op.execute("UPDATE documents SET status = 'COMPLETED_WITH_WARNINGS' WHERE length(status) > 32")
    op.alter_column("documents", "status", type_=sa.String(32), existing_type=sa.String(64))
