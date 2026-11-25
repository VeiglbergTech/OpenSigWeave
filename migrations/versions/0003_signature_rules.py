"""Add persistent signature rules and identity conditions."""
from alembic import op
import sqlalchemy as sa

revision = "0003_signature_rules"
down_revision = "0002_signature_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signature_rules",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("action_mode", sa.String(length=32), nullable=False),
        sa.Column("signature_id", sa.String(length=32), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["signature_id"], ["signatures.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "signature_rule_conditions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["rule_id"], ["signature_rules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_signature_rule_conditions_rule_id",
        "signature_rule_conditions",
        ["rule_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_signature_rule_conditions_rule_id", table_name="signature_rule_conditions")
    op.drop_table("signature_rule_conditions")
    op.drop_table("signature_rules")
