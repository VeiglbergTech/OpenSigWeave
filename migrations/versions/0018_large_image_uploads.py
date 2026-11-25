"""Configurable 6/12 MiB signature and branding upload limits."""
from alembic import op
import sqlalchemy as sa

revision = "0018_large_image_uploads"
down_revision = "0017_backup_rule_management"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("branding_settings", sa.Column("large_image_uploads", sa.Boolean(), nullable=False, server_default=sa.false()))

def downgrade() -> None:
    op.drop_column("branding_settings", "large_image_uploads")
