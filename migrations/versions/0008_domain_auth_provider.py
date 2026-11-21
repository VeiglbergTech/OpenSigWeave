"""Add per-domain authentication provider selection."""
from alembic import op
import sqlalchemy as sa

revision = "0008_domain_auth_provider"
down_revision = "0007_managed_idps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("domains") as batch_op:
        batch_op.add_column(sa.Column("authentication_provider_id", sa.String(length=64), nullable=True))
        batch_op.create_foreign_key(
            "fk_domains_authentication_provider_id", "identity_providers",
            ["authentication_provider_id"], ["id"], ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_domains_authentication_provider_id", ["authentication_provider_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("domains") as batch_op:
        batch_op.drop_index("ix_domains_authentication_provider_id")
        batch_op.drop_constraint("fk_domains_authentication_provider_id", type_="foreignkey")
        batch_op.drop_column("authentication_provider_id")
