"""Add optional dark mode logo while preserving existing standard branding."""
from alembic import op
import sqlalchemy as sa

revision = "0015_dark_mode_branding_logo"
down_revision = "0014_retire_signature_modes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("branding_settings", sa.Column("dark_logo_png", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("branding_settings", "dark_logo_png")
