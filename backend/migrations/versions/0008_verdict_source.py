"""Record whether a verdict came from rules or Gemini."""
from alembic import op
import sqlalchemy as sa

revision = "0008_verdict_source"
down_revision = "0007_pending_verification"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("contradictions", sa.Column("source", sa.String(16), nullable=False,
                                               server_default="gemini"))


def downgrade():
    op.drop_column("contradictions", "source")
