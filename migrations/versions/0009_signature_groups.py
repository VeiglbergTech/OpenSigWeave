"""Create reusable signature groups and group memberships.

The migration is additive: existing mode and variant rows remain untouched
until the compatibility migration is implemented and validated separately.
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_signature_groups"
down_revision = "0008_domain_auth_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signature_groups",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column(
            "selection_policy",
            sa.String(length=40),
            server_default="WEIGHTED_RANDOM_NO_REPEAT",
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_signature_groups_slug", "signature_groups", ["slug"], unique=True)

    op.create_table(
        "signature_group_domains",
        sa.Column("group_id", sa.String(length=32), nullable=False),
        sa.Column("domain_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["signature_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("group_id", "domain_id"),
    )

    op.add_column("signature_rules", sa.Column("group_id", sa.String(length=32), nullable=True))
    op.create_foreign_key(
        "fk_signature_rules_group_id", "signature_rules", "signature_groups",
        ["group_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_signature_rules_group_id", "signature_rules", ["group_id"], unique=False)

    op.create_table(
        "signature_group_members",
        sa.Column("group_id", sa.String(length=32), nullable=False),
        sa.Column("signature_id", sa.String(length=32), nullable=False),
        sa.Column("weight", sa.Integer(), server_default="1", nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.CheckConstraint("weight > 0", name="ck_signature_group_member_weight_positive"),
        sa.ForeignKeyConstraint(["group_id"], ["signature_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["signature_id"], ["signatures.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("group_id", "signature_id"),
    )


def downgrade() -> None:
    op.drop_index("ix_signature_rules_group_id", table_name="signature_rules")
    op.drop_constraint("fk_signature_rules_group_id", "signature_rules", type_="foreignkey")
    op.drop_column("signature_rules", "group_id")
    op.drop_table("signature_group_members")
    op.drop_table("signature_group_domains")
    op.drop_index("ix_signature_groups_slug", table_name="signature_groups")
    op.drop_table("signature_groups")
