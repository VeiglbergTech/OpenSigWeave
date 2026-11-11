"""Persist the last successful Authentik identity attributes."""
from alembic import op
import sqlalchemy as sa

revision = "0005_identity_attribute_cache"
down_revision = "0004_rate_limit_buckets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_attribute_cache",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("first_name", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("last_name", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("title", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("phone", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("groups", sa.JSON(), nullable=False),
        sa.Column("claims", sa.JSON(), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("email"),
    )


def downgrade() -> None:
    op.drop_table("identity_attribute_cache")
