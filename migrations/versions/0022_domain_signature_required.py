"""Add domain-wide SMTP signature requirement without altering existing rules."""
from alembic import op
import sqlalchemy as sa

revision = "0022_domain_signature_required"
down_revision = "0021_mail_adapter_activity"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "domains",
        sa.Column("signature_required", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("domains", "signature_required")
