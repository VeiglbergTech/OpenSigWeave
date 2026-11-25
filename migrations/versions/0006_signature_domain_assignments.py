"""Introduce many-to-many signature/domain assignments, preserving existing links."""
from alembic import op
import sqlalchemy as sa

revision = "0006_sig_domain_map"
down_revision = "0005_identity_attribute_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signature_domains",
        sa.Column("signature_id", sa.String(length=32), nullable=False),
        sa.Column("domain_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["signature_id"], ["signatures.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("signature_id", "domain_id"),
    )
    op.execute(
        "INSERT INTO signature_domains (signature_id, domain_id) "
        "SELECT id, domain_id FROM signatures WHERE domain_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_table("signature_domains")
