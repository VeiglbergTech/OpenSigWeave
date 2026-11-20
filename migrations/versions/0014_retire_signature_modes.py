"""Retire legacy signature modes while preserving existing installations.

The old signature.mode, signature_variants and selection_state storage remains in
place for migration/rollback compatibility only. Runtime selection uses direct
signatures, signature groups, rules and schedules.
"""
import uuid

from alembic import op
import sqlalchemy as sa

revision = "0014_retire_signature_modes"
down_revision = "0013_branding_schedules"
branch_labels = None
depends_on = None


LEGACY_POOLS = (
    ("AUTO", 1),
    ("PROFESSIONAL", 2),
    ("INTERNAL", 3),
)


def _group_id(pool_name: str) -> str:
    return uuid.uuid5(
        uuid.NAMESPACE_URL, f"opensigweave:legacy-group:{pool_name}"
    ).hex


def _ensure_compat_group(connection, pool_name: str, ordinal: int) -> str:
    group_id = _group_id(pool_name)
    row = connection.execute(
        sa.text("SELECT id, slug FROM signature_groups WHERE id = :id"),
        {"id": group_id},
    ).mappings().first()

    if row is None:
        connection.execute(
            sa.text(
                "INSERT INTO signature_groups "
                "(id, name, slug, selection_policy, is_active) "
                "VALUES (:id, :name, :slug, :policy, :active)"
            ),
            {
                "id": group_id,
                "name": f"Migrated signature group {ordinal}",
                "slug": f"compat-{group_id}",
                "policy": "WEIGHTED_RANDOM_NO_REPEAT",
                "active": True,
            },
        )
    elif row["slug"] == f"legacy-{pool_name.casefold()}":
        new_slug = f"compat-{group_id}"
        conflict = connection.execute(
            sa.text(
                "SELECT id FROM signature_groups "
                "WHERE slug = :slug AND id <> :id"
            ),
            {"slug": new_slug, "id": group_id},
        ).first()
        if conflict is None:
            connection.execute(
                sa.text(
                    "UPDATE signature_groups "
                    "SET name = :name, slug = :slug WHERE id = :id"
                ),
                {
                    "id": group_id,
                    "name": f"Migrated signature group {ordinal}",
                    "slug": new_slug,
                },
            )

    variant_rows = connection.execute(
        sa.text(
            "SELECT signature_id, "
            "SUM(CASE WHEN is_enabled THEN weight ELSE 0 END) AS weight, "
            "MAX(CASE WHEN is_enabled THEN 1 ELSE 0 END) AS enabled "
            "FROM signature_variants WHERE pool_name = :pool "
            "GROUP BY signature_id"
        ),
        {"pool": pool_name},
    ).mappings()
    for variant in variant_rows:
        exists = connection.execute(
            sa.text(
                "SELECT 1 FROM signature_group_members "
                "WHERE group_id = :group_id AND signature_id = :signature_id"
            ),
            {
                "group_id": group_id,
                "signature_id": variant["signature_id"],
            },
        ).first()
        if exists is None:
            connection.execute(
                sa.text(
                    "INSERT INTO signature_group_members "
                    "(group_id, signature_id, weight, is_enabled) "
                    "VALUES (:group_id, :signature_id, :weight, :enabled)"
                ),
                {
                    "group_id": group_id,
                    "signature_id": variant["signature_id"],
                    "weight": max(1, int(variant["weight"] or 1)),
                    "enabled": bool(variant["enabled"]),
                },
            )
    return group_id


def upgrade() -> None:
    connection = op.get_bind()

    for pool_name, ordinal in LEGACY_POOLS:
        group_id = _ensure_compat_group(connection, pool_name, ordinal)
        # A legacy rule with a concrete signature target already expressed
        # exact-selection intent. Preserve that behavior as a direct rule.
        connection.execute(
            sa.text(
                "UPDATE signature_rules "
                "SET action_mode = 'SIGNATURE', group_id = NULL "
                "WHERE action_mode = :pool AND signature_id IS NOT NULL"
            ),
            {"pool": pool_name},
        )
        # Untargeted legacy pool rules become ordinary signature groups.
        connection.execute(
            sa.text(
                "UPDATE signature_rules "
                "SET action_mode = 'GROUP', group_id = :group_id, signature_id = NULL "
                "WHERE action_mode = :pool"
            ),
            {"group_id": group_id, "pool": pool_name},
        )

    connection.execute(
        sa.text(
            "UPDATE signatures SET mode = 'UNUSED' "
            "WHERE mode <> 'UNUSED'"
        )
    )

    # Earlier migrations created one generated group per historical pool even on
    # a fresh database. Remove only those generated groups that are truly empty
    # and unreferenced so new installations expose no compatibility artifacts.
    for pool_name, _ordinal in LEGACY_POOLS:
        group_id = _group_id(pool_name)
        has_members = connection.execute(
            sa.text("SELECT 1 FROM signature_group_members WHERE group_id = :id LIMIT 1"),
            {"id": group_id},
        ).first()
        has_rules = connection.execute(
            sa.text("SELECT 1 FROM signature_rules WHERE group_id = :id LIMIT 1"),
            {"id": group_id},
        ).first()
        has_schedules = connection.execute(
            sa.text("SELECT 1 FROM signature_schedules WHERE group_id = :id LIMIT 1"),
            {"id": group_id},
        ).first()
        if has_members or has_rules or has_schedules:
            continue
        connection.execute(
            sa.text("DELETE FROM signature_group_domains WHERE group_id = :id"),
            {"id": group_id},
        )
        connection.execute(
            sa.text("DELETE FROM group_selection_state WHERE group_id = :id"),
            {"id": group_id},
        )
        connection.execute(
            sa.text("DELETE FROM signature_groups WHERE id = :id"),
            {"id": group_id},
        )


def downgrade() -> None:
    connection = op.get_bind()

    for pool_name, ordinal in LEGACY_POOLS:
        group_id = _group_id(pool_name)
        connection.execute(
            sa.text(
                "UPDATE signature_rules "
                "SET action_mode = :pool, group_id = NULL "
                "WHERE action_mode = 'GROUP' AND group_id = :group_id"
            ),
            {"pool": pool_name, "group_id": group_id},
        )
        connection.execute(
            sa.text(
                "UPDATE signature_groups "
                "SET name = :name, slug = :slug "
                "WHERE id = :id AND slug = :compat_slug"
            ),
            {
                "id": group_id,
                "name": f"Legacy {pool_name.title()}",
                "slug": f"legacy-{pool_name.casefold()}",
                "compat_slug": f"compat-{group_id}",
            },
        )

    rows = connection.execute(
        sa.text("SELECT id FROM signatures WHERE mode = 'UNUSED'")
    ).mappings()
    for row in rows:
        pool = connection.execute(
            sa.text(
                "SELECT pool_name FROM signature_variants "
                "WHERE signature_id = :signature_id "
                "AND pool_name IN ('AUTO', 'PROFESSIONAL', 'INTERNAL') "
                "ORDER BY id LIMIT 1"
            ),
            {"signature_id": row["id"]},
        ).scalar()
        connection.execute(
            sa.text("UPDATE signatures SET mode = :mode WHERE id = :id"),
            {"mode": pool or "AUTO", "id": row["id"]},
        )
