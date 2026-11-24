"""Persist per-domain rendering fallback values (empty by default)."""

from alembic import op
import sqlalchemy as sa

revision = "0019_domain_fallback_values"
down_revision = "0018_large_image_uploads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "domains",
        sa.Column("fallback_values", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("domains", "fallback_values")
