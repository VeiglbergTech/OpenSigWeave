"""Add stable principal identity and group rotation state."""
from alembic import op
import sqlalchemy as sa

revision = "0010_group_selection_state"
down_revision = "0009_signature_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("identity_attribute_cache") as batch_op:
        batch_op.add_column(sa.Column("principal_id", sa.String(length=256), nullable=True))

    op.create_table(
        "group_selection_state",
        sa.Column("principal_id", sa.String(length=320), nullable=False),
        sa.Column("group_id", sa.String(length=32), nullable=False),
        sa.Column("last_signature_id", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["signature_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["last_signature_id"], ["signatures.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("principal_id", "group_id"),
    )


def downgrade() -> None:
    op.drop_table("group_selection_state")
    with op.batch_alter_table("identity_attribute_cache") as batch_op:
        batch_op.drop_column("principal_id")
