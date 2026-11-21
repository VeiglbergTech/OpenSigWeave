"""Add persistent branding and time-bounded signature schedules."""
from alembic import op
import sqlalchemy as sa

revision = "0013_branding_schedules"
down_revision = "0012_html_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "branding_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("app_name", sa.String(length=120), server_default="OpenSigWeave", nullable=False),
        sa.Column("app_color", sa.String(length=7), server_default="#ff7043", nullable=False),
        sa.Column("logo_png", sa.LargeBinary(), nullable=True),
        sa.Column("favicon_data", sa.LargeBinary(), nullable=True),
        sa.Column("favicon_mime", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "signature_schedules",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("domain_id", sa.Integer(), nullable=True),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("signature_id", sa.String(length=32), nullable=True),
        sa.Column("group_id", sa.String(length=32), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("starts_at < ends_at", name="ck_signature_schedule_window"),
        sa.CheckConstraint(
            "(target_type = 'SIGNATURE' AND signature_id IS NOT NULL AND group_id IS NULL) "
            "OR (target_type = 'GROUP' AND group_id IS NOT NULL AND signature_id IS NULL)",
            name="ck_signature_schedule_target",
        ),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["signature_id"], ["signatures.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["group_id"], ["signature_groups.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_signature_schedules_domain_id", "signature_schedules", ["domain_id"])
    op.create_index("ix_signature_schedules_signature_id", "signature_schedules", ["signature_id"])
    op.create_index("ix_signature_schedules_group_id", "signature_schedules", ["group_id"])
    op.create_index("ix_signature_schedules_starts_at", "signature_schedules", ["starts_at"])
    op.create_index("ix_signature_schedules_ends_at", "signature_schedules", ["ends_at"])


def downgrade() -> None:
    op.drop_index("ix_signature_schedules_ends_at", table_name="signature_schedules")
    op.drop_index("ix_signature_schedules_starts_at", table_name="signature_schedules")
    op.drop_index("ix_signature_schedules_group_id", table_name="signature_schedules")
    op.drop_index("ix_signature_schedules_signature_id", table_name="signature_schedules")
    op.drop_index("ix_signature_schedules_domain_id", table_name="signature_schedules")
    op.drop_table("signature_schedules")
    op.drop_table("branding_settings")
