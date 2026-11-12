"""Add managed identity providers and per-domain provider selection."""
from alembic import op
import sqlalchemy as sa

revision = "0007_managed_idps"
down_revision = "0006_sig_domain_map"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_providers",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("provider_type", sa.String(length=32), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.add_column("domains", sa.Column("identity_provider_id", sa.String(length=64), nullable=True))
    op.create_foreign_key(
        "fk_domains_identity_provider_id", "domains", "identity_providers",
        ["identity_provider_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_domains_identity_provider_id", "domains", type_="foreignkey")
    op.drop_column("domains", "identity_provider_id")
    op.drop_table("identity_providers")
