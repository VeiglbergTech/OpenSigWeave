"""Web-bootstrap admin and persisted mail adapter health settings."""
from alembic import op
import sqlalchemy as sa

revision = "0020_system_setup"
down_revision = "0019_domain_fallback_values"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "system_setup",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mail_server", sa.String(32), nullable=False, server_default="unconfigured"),
        sa.Column("monitoring_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("monitoring_interval", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("controller_url", sa.String(255), nullable=False, server_default=""),
        sa.Column("setup_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_monitor_status", sa.String(32), nullable=False, server_default="not_checked"),
        sa.Column("last_monitor_detail", sa.String(255), nullable=False, server_default=""),
        sa.Column("last_monitored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("monitoring_interval BETWEEN 30 AND 3600", name="ck_system_monitor_interval"),
    )
    op.create_table(
        "local_admin",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

def downgrade() -> None:
    op.drop_table("local_admin")
    op.drop_table("system_setup")
