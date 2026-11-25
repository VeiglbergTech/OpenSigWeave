"""Schedule-driven encrypted backup rules and durable run history."""
from alembic import op
import sqlalchemy as sa

revision = "0017_backup_rule_management"
down_revision = "0016_backup_locations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backup_rules",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("location_id", sa.String(32), sa.ForeignKey("backup_locations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("content", sa.String(16), nullable=False, server_default="full"),
        sa.Column("schedule_kind", sa.String(16), nullable=False),
        sa.Column("hour", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("minute", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("weekday", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("monthday", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("interval_minutes", sa.Integer(), nullable=False, server_default="1440"),
        sa.Column("timezone", sa.String(16), nullable=False, server_default="UTC"),
        sa.Column("retention_count", sa.Integer(), nullable=True),
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("content = 'full'", name="ck_backup_rule_content"),
        sa.CheckConstraint("schedule_kind IN ('daily','weekly','monthly','interval')", name="ck_backup_rule_schedule"),
        sa.CheckConstraint("timezone = 'UTC'", name="ck_backup_rule_timezone"),
        sa.CheckConstraint("retention_count IS NOT NULL OR retention_days IS NOT NULL", name="ck_backup_rule_retention"),
    )
    op.create_index("ix_backup_rules_location_id", "backup_rules", ["location_id"])
    op.create_index("ix_backup_rules_next_run_at", "backup_rules", ["next_run_at"])
    op.create_table(
        "backup_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("rule_id", sa.String(32), sa.ForeignKey("backup_rules.id", ondelete="SET NULL"), nullable=True),
        sa.Column("rule_name", sa.String(120), nullable=False),
        sa.Column("location_id", sa.String(32), nullable=False),
        sa.Column("location_name", sa.String(120), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("content", sa.String(16), nullable=False, server_default="full"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transfer_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("archive_sha256", sa.String(64), nullable=True),
        sa.Column("file_key", sa.String(100), nullable=True),
        sa.Column("is_retained", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error_detail", sa.String(300), nullable=True),
    )
    op.create_index("ix_backup_runs_rule_id", "backup_runs", ["rule_id"])
    op.create_index("ix_backup_runs_location_id", "backup_runs", ["location_id"])
    op.create_table("backup_runtime",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("active_run_id", sa.String(32), nullable=True),
    )
    op.execute(sa.text("INSERT INTO backup_runtime (id, active_run_id) VALUES (1, NULL)"))


def downgrade() -> None:
    op.drop_table("backup_runtime")
    op.drop_index("ix_backup_runs_location_id", table_name="backup_runs")
    op.drop_index("ix_backup_runs_rule_id", table_name="backup_runs")
    op.drop_table("backup_runs")
    op.drop_index("ix_backup_rules_next_run_at", table_name="backup_rules")
    op.drop_index("ix_backup_rules_location_id", table_name="backup_rules")
    op.drop_table("backup_rules")
