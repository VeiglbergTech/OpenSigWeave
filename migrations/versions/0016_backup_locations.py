"""Add independent, encrypted-credential backup-location registry.

Existing host-operator backup settings and archived backups are untouched.
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_backup_locations"
down_revision = "0015_dark_mode_branding_logo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backup_locations",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False, unique=True),
        sa.Column("type", sa.String(length=8), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("server", sa.String(length=253), nullable=True),
        sa.Column("share", sa.String(length=80), nullable=True),
        sa.Column("directory", sa.String(length=512), nullable=True),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("domain", sa.String(length=128), nullable=True),
        sa.Column("password_ciphertext", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("type IN ('local', 'smb')", name="ck_backup_location_type"),
    )


def downgrade() -> None:
    op.drop_table("backup_locations")
