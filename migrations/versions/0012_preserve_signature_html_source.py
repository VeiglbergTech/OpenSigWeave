"""Add source-preserving HTML columns for legacy signature records.

Existing sanitized content is copied as the initial source snapshot. Previously
removed markup cannot be reconstructed by this migration.
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_preserve_signature_html_source"
down_revision = "0011_legacy_signature_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("domains", sa.Column("template_html_source", sa.Text(), nullable=True))
    op.add_column("user_overrides", sa.Column("html_content_source", sa.Text(), nullable=True))

    connection = op.get_bind()
    connection.execute(sa.text(
        "UPDATE domains SET template_html_source = template_html "
        "WHERE template_html_source IS NULL"
    ))
    connection.execute(sa.text(
        "UPDATE user_overrides SET html_content_source = html_content "
        "WHERE html_content_source IS NULL"
    ))


def downgrade() -> None:
    connection = op.get_bind()
    domain_source_changes = connection.execute(sa.text(
        "SELECT COUNT(*) FROM domains "
        "WHERE template_html_source IS NOT NULL "
        "AND template_html_source IS DISTINCT FROM template_html"
    )).scalar_one()
    override_source_changes = connection.execute(sa.text(
        "SELECT COUNT(*) FROM user_overrides "
        "WHERE html_content_source IS NOT NULL "
        "AND html_content_source IS DISTINCT FROM html_content"
    )).scalar_one()
    if domain_source_changes or override_source_changes:
        raise RuntimeError(
            "Refusing to drop preserved signature HTML source: source content "
            "differs from sanitized content. Export or back up source HTML first."
        )
    op.drop_column("user_overrides", "html_content_source")
    op.drop_column("domains", "template_html_source")
