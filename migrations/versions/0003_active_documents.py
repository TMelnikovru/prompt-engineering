"""Keep removed upstream records in history while hiding them from current lists."""

import sqlalchemy as sa
from alembic import op

revision = "0003_active_documents"
down_revision = "0002_ingestion_store"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source_documents",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("source_documents", "is_active")
