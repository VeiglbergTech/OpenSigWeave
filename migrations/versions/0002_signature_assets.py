"""Add hybrid signature versions, variants and sender selection state."""
from alembic import op
import sqlalchemy as sa

revision = "0002_signature_assets"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signatures",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("domain_id", sa.Integer(), nullable=True),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_signatures_domain_id", "signatures", ["domain_id"], unique=False)

    op.create_table(
        "signature_versions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("signature_id", sa.String(length=32), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("relative_path", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=320), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["signature_id"], ["signatures.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("signature_id", "version_number", name="uq_signature_version_number"),
        sa.UniqueConstraint("relative_path"),
    )
    op.create_index("ix_signature_versions_signature_id", "signature_versions", ["signature_id"], unique=False)

    op.create_table(
        "signature_variants",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("signature_id", sa.String(length=32), nullable=False),
        sa.Column("pool_name", sa.String(length=32), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.CheckConstraint("weight > 0", name="ck_signature_variant_weight_positive"),
        sa.ForeignKeyConstraint(["signature_id"], ["signatures.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_signature_variants_signature_id", "signature_variants", ["signature_id"], unique=False)

    op.create_table(
        "selection_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sender_email", sa.String(length=320), nullable=False),
        sa.Column("pool_name", sa.String(length=32), nullable=False),
        sa.Column("last_variant_id", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["last_variant_id"], ["signature_variants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sender_email", "pool_name", name="uq_selection_sender_pool"),
    )


def downgrade() -> None:
    op.drop_table("selection_state")
    op.drop_index("ix_signature_variants_signature_id", table_name="signature_variants")
    op.drop_table("signature_variants")
    op.drop_index("ix_signature_versions_signature_id", table_name="signature_versions")
    op.drop_table("signature_versions")
    op.drop_index("ix_signatures_domain_id", table_name="signatures")
    op.drop_table("signatures")
