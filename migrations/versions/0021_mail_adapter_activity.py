"""Track authenticated mail-adapter activity separately from advanced probes."""
from alembic import op
import sqlalchemy as sa

revision = "0021_mail_adapter_activity"
down_revision = "0020_system_setup"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("system_setup", sa.Column("last_authenticated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("system_setup", sa.Column("engine_url", sa.String(512), nullable=False, server_default=""))
    # Previously saved forms do not prove authenticated adapter interaction.
    op.execute("UPDATE system_setup SET setup_completed = FALSE")

def downgrade():
    op.drop_column("system_setup", "engine_url")
    op.drop_column("system_setup", "last_authenticated_at")
