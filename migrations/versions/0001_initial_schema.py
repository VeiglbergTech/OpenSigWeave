"""Create the original domains and user_overrides tables.

Existing SQLite installations are detected and stamped forward without
recreating their existing tables. This allows a controlled adoption of
Alembic without dropping the current user data.
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("domains"):
        op.create_table(
            "domains",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("domain_name", sa.String(), nullable=True),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=True),
            sa.Column("allow_overrides", sa.Boolean(), server_default=sa.true(), nullable=True),
            sa.Column("inject_on_replies", sa.Boolean(), server_default=sa.false(), nullable=True),
            sa.Column("trim_whitespace", sa.Boolean(), server_default=sa.true(), nullable=True),
            sa.Column("strip_device_signatures", sa.Boolean(), server_default=sa.false(), nullable=True),
            sa.Column(
                "template_html",
                sa.Text(),
                server_default=(
                    "<p>Best Regards,</p><p><br></p><p><strong>{{ first_name }} "
                    "{{ last_name }}</strong></p><p>{{ title }} | {{ domain_name }}</p>"
                ),
                nullable=True,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_domains_id", "domains", ["id"], unique=False)
        op.create_index("ix_domains_domain_name", "domains", ["domain_name"], unique=True)

    if not inspector.has_table("user_overrides"):
        op.create_table(
            "user_overrides",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_email", sa.String(), nullable=True),
            sa.Column("html_content", sa.Text(), server_default="", nullable=True),
            sa.Column("is_disabled", sa.Boolean(), server_default=sa.false(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_user_overrides_id", "user_overrides", ["id"], unique=False)
        op.create_index("ix_user_overrides_user_email", "user_overrides", ["user_email"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("user_overrides"):
        op.drop_table("user_overrides")
    if inspector.has_table("domains"):
        op.drop_table("domains")
