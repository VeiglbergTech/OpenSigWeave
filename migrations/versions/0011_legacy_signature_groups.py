"""Seed reusable groups for the legacy signature modes without changing rules."""
import uuid

from alembic import op
import sqlalchemy as sa

revision = "0011_legacy_signature_groups"
down_revision = "0010_group_selection_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    pool_names = ("AUTO", "PROFESSIONAL", "INTERNAL")
    group_ids = {}
    for pool_name in pool_names:
        group_id = uuid.uuid5(uuid.NAMESPACE_URL, f"opensigweave:legacy-group:{pool_name}").hex
        group_ids[pool_name] = group_id
        connection.execute(
            sa.text(
                "INSERT INTO signature_groups (id, name, slug, selection_policy, is_active) "
                "VALUES (:id, :name, :slug, :policy, :active)"
            ),
            {
                "id": group_id,
                "name": f"Legacy {pool_name.title()}",
                "slug": f"legacy-{pool_name.casefold()}",
                "policy": "WEIGHTED_RANDOM_NO_REPEAT",
                "active": True,
            },
        )

    rows = connection.execute(
        sa.text(
            "SELECT signature_id, pool_name, MAX(weight) AS weight, "
            "MAX(CASE WHEN is_enabled THEN 1 ELSE 0 END) AS enabled "
            "FROM signature_variants "
            "WHERE pool_name IN ('AUTO', 'PROFESSIONAL', 'INTERNAL') "
            "GROUP BY signature_id, pool_name"
        )
    ).mappings()
    for row in rows:
        connection.execute(
            sa.text(
                "INSERT INTO signature_group_members "
                "(group_id, signature_id, weight, is_enabled) "
                "VALUES (:group_id, :signature_id, :weight, :enabled)"
            ),
            {
                "group_id": group_ids[row["pool_name"]],
                "signature_id": row["signature_id"],
                "weight": max(1, int(row["weight"] or 1)),
                "enabled": bool(row["enabled"]),
            },
        )


def downgrade() -> None:
    connection = op.get_bind()
    for pool_name in ("AUTO", "PROFESSIONAL", "INTERNAL"):
        slug = f"legacy-{pool_name.casefold()}"
        connection.execute(
            sa.text("DELETE FROM signature_groups WHERE slug = :slug"),
            {"slug": slug},
        )
