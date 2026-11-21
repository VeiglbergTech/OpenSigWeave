"""Integration test: adopting Alembic must preserve legacy SQLite data."""
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class LegacyMigrationCompatibilityTests(unittest.TestCase):
    def test_existing_tables_are_preserved_and_revision_is_recorded(self):
        with tempfile.TemporaryDirectory(prefix="opensigweave-migration-") as tmp:
            database_path = pathlib.Path(tmp) / "legacy.db"
            connection = sqlite3.connect(database_path)
            connection.executescript(
                """
                CREATE TABLE domains (
                    id INTEGER NOT NULL PRIMARY KEY,
                    domain_name VARCHAR,
                    is_active BOOLEAN,
                    allow_overrides BOOLEAN,
                    inject_on_replies BOOLEAN,
                    trim_whitespace BOOLEAN,
                    strip_device_signatures BOOLEAN,
                    template_html TEXT
                );
                CREATE UNIQUE INDEX ix_domains_domain_name ON domains(domain_name);
                CREATE INDEX ix_domains_id ON domains(id);
                CREATE TABLE user_overrides (
                    id INTEGER NOT NULL PRIMARY KEY,
                    user_email VARCHAR,
                    html_content TEXT,
                    is_disabled BOOLEAN
                );
                CREATE UNIQUE INDEX ix_user_overrides_user_email ON user_overrides(user_email);
                CREATE INDEX ix_user_overrides_id ON user_overrides(id);
                INSERT INTO domains VALUES (1, 'example.org', 1, 1, 0, 1, 0, '<p>Keep me</p>');
                INSERT INTO user_overrides VALUES (1, 'alice@example.org', '<p>Existing</p>', 0);
                """
            )
            connection.commit()
            connection.close()

            environment = os.environ.copy()
            environment["DATABASE_URL"] = f"sqlite:///{database_path}"
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "upgrade", "head"],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            connection = sqlite3.connect(database_path)
            domain = connection.execute(
                "SELECT domain_name, template_html, template_html_source FROM domains WHERE id = 1"
            ).fetchone()
            override = connection.execute(
                "SELECT user_email, html_content, html_content_source FROM user_overrides WHERE id = 1"
            ).fetchone()
            revision = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            columns = {row[1] for row in connection.execute("PRAGMA table_info(domains)")}
            group_member_columns = {row[1] for row in connection.execute("PRAGMA table_info(signature_group_members)")}
            rule_columns = {row[1] for row in connection.execute("PRAGMA table_info(signature_rules)")}

            self.assertEqual(domain, ("example.org", "<p>Keep me</p>", "<p>Keep me</p>"))
            self.assertEqual(override, ("alice@example.org", "<p>Existing</p>", "<p>Existing</p>"))
            self.assertEqual(revision, ("0014_retire_signature_modes",))
            self.assertIn("signatures", tables)
            self.assertIn("signature_versions", tables)
            self.assertIn("signature_variants", tables)
            self.assertIn("selection_state", tables)
            self.assertIn("signature_rules", tables)
            self.assertIn("signature_rule_conditions", tables)
            self.assertIn("rate_limit_buckets", tables)
            self.assertIn("identity_attribute_cache", tables)
            self.assertIn("signature_domains", tables)
            self.assertIn("identity_providers", tables)
            self.assertIn("signature_groups", tables)
            self.assertIn("signature_group_domains", tables)
            self.assertIn("signature_group_members", tables)
            self.assertIn("group_selection_state", tables)
            self.assertIn("signature_schedules", tables)
            self.assertIn("branding_settings", tables)
            self.assertIn("identity_provider_id", columns)
            self.assertIn("authentication_provider_id", columns)
            self.assertIn("template_html_source", columns)
            override_columns = {row[1] for row in connection.execute("PRAGMA table_info(user_overrides)")}
            self.assertIn("html_content_source", override_columns)

            self.assertTrue({"group_id", "signature_id", "weight", "is_enabled"}.issubset(group_member_columns))
            self.assertIn("group_id", rule_columns)
            cache_columns = {row[1] for row in connection.execute("PRAGMA table_info(identity_attribute_cache)")}
            self.assertIn("principal_id", cache_columns)
            compatibility_groups = connection.execute("SELECT name, slug FROM signature_groups ORDER BY slug").fetchall()
            self.assertEqual(compatibility_groups, [])
            connection.close()


    def test_legacy_group_seed_is_converted_to_current_group_rules(self):
        with tempfile.TemporaryDirectory(prefix="opensigweave-group-seed-") as tmp:
            database_path = pathlib.Path(tmp) / "groups.db"
            environment = os.environ.copy()
            environment["DATABASE_URL"] = f"sqlite:///{database_path}"
            upgrade = subprocess.run(
                [
                    sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"),
                    "upgrade", "0010_group_selection_state",
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(upgrade.returncode, 0, upgrade.stdout + upgrade.stderr)

            connection = sqlite3.connect(database_path)
            connection.executescript(
                """
                INSERT INTO domains (id, domain_name, is_active)
                VALUES (1, 'example.org', 1);

                INSERT INTO signatures (id, name, domain_id, scope, mode, is_active)
                VALUES
                    ('sig-auto-1', 'Auto One', 1, 'DOMAIN', 'AUTO', 1),
                    ('sig-auto-2', 'Auto Two', 1, 'DOMAIN', 'AUTO', 1),
                    ('sig-prof-1', 'Professional One', 1, 'DOMAIN', 'PROFESSIONAL', 1),
                    ('sig-int-1', 'Internal One', 1, 'DOMAIN', 'INTERNAL', 1);

                INSERT INTO signature_variants (id, signature_id, pool_name, weight, is_enabled)
                VALUES
                    ('v-auto-1a', 'sig-auto-1', 'AUTO', 2, 1),
                    ('v-auto-1b', 'sig-auto-1', 'AUTO', 3, 1),
                    ('v-auto-2', 'sig-auto-2', 'AUTO', 7, 0),
                    ('v-prof-1', 'sig-prof-1', 'PROFESSIONAL', 4, 1),
                    ('v-int-1', 'sig-int-1', 'INTERNAL', 6, 1);

                INSERT INTO signature_rules (id, name, priority, action_mode, signature_id, is_enabled)
                VALUES
                    ('rule-auto', 'Existing AUTO rule', 10, 'AUTO', NULL, 1),
                    ('rule-direct', 'Existing direct rule', 20, 'PROFESSIONAL', 'sig-prof-1', 1);
                """
            )
            connection.commit()
            connection.close()

            migrated = subprocess.run(
                [
                    sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"),
                    "upgrade", "head",
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(migrated.returncode, 0, migrated.stdout + migrated.stderr)

            connection = sqlite3.connect(database_path)
            auto_group_id = connection.execute(
                "SELECT group_id FROM signature_group_members WHERE signature_id = 'sig-auto-1' LIMIT 1"
            ).fetchone()[0]
            professional_group_id = connection.execute(
                "SELECT group_id FROM signature_group_members WHERE signature_id = 'sig-prof-1' LIMIT 1"
            ).fetchone()[0]
            internal_group_id = connection.execute(
                "SELECT group_id FROM signature_group_members WHERE signature_id = 'sig-int-1' LIMIT 1"
            ).fetchone()[0]
            auto_members = {
                signature_id: (weight, bool(enabled))
                for signature_id, weight, enabled in connection.execute(
                    "SELECT signature_id, weight, is_enabled FROM signature_group_members WHERE group_id = ?",
                    (auto_group_id,),
                ).fetchall()
            }
            professional_members = connection.execute(
                "SELECT signature_id, weight, is_enabled FROM signature_group_members WHERE group_id = ?",
                (professional_group_id,),
            ).fetchall()
            internal_members = connection.execute(
                "SELECT signature_id, weight, is_enabled FROM signature_group_members WHERE group_id = ?",
                (internal_group_id,),
            ).fetchall()
            existing_rule = connection.execute(
                "SELECT action_mode, group_id, priority FROM signature_rules WHERE id = 'rule-auto'"
            ).fetchone()
            direct_rule = connection.execute(
                "SELECT action_mode, signature_id, group_id FROM signature_rules WHERE id = 'rule-direct'"
            ).fetchone()
            signature_modes = connection.execute("SELECT DISTINCT mode FROM signatures").fetchall()
            group_slugs = connection.execute("SELECT slug FROM signature_groups ORDER BY slug").fetchall()
            revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            connection.close()

            self.assertEqual(auto_members, {
                "sig-auto-1": (5, True),
                "sig-auto-2": (1, False),
            })
            self.assertEqual(professional_members, [("sig-prof-1", 4, 1)])
            self.assertEqual(internal_members, [("sig-int-1", 6, 1)])
            self.assertEqual(existing_rule, ("GROUP", auto_group_id, 10))
            self.assertEqual(direct_rule, ("SIGNATURE", "sig-prof-1", None))
            self.assertEqual(signature_modes, [("UNUSED",)])
            self.assertTrue(all(slug.startswith("compat-") for (slug,) in group_slugs))
            self.assertEqual(revision, ("0014_retire_signature_modes",))


    def test_scoped_empty_compatibility_group_survives_mode_retirement(self):
        # An administrator may scope an otherwise empty seed group. The scope
        # is user data and must not be deleted by compatibility cleanup.
        with tempfile.TemporaryDirectory(prefix="opensigweave-scoped-legacy-") as tmp:
            database_path = pathlib.Path(tmp) / "scoped.db"
            environment = os.environ.copy()
            environment["DATABASE_URL"] = f"sqlite:///{database_path}"

            def migrate(target):
                result = subprocess.run(
                    [
                        sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"),
                        "upgrade", target,
                    ],
                    cwd=ROOT, env=environment, capture_output=True,
                    text=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            migrate("0013_branding_schedules")
            connection = sqlite3.connect(database_path)
            connection.execute(
                "INSERT INTO domains (id, domain_name, is_active) VALUES (?, ?, ?)",
                (1, "scoped.example.org", 1),
            )
            group_id = connection.execute(
                "SELECT id FROM signature_groups WHERE slug = 'legacy-auto'"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO signature_group_domains (group_id, domain_id) VALUES (?, ?)",
                (group_id, 1),
            )
            connection.commit()
            connection.close()

            migrate("head")
            connection = sqlite3.connect(database_path)
            preserved_group = connection.execute(
                "SELECT name, slug FROM signature_groups WHERE id = ?",
                (group_id,),
            ).fetchone()
            preserved_scope = connection.execute(
                "SELECT domain_id FROM signature_group_domains WHERE group_id = ?",
                (group_id,),
            ).fetchall()
            other_generated_groups = connection.execute(
                "SELECT COUNT(*) FROM signature_groups WHERE id <> ?",
                (group_id,),
            ).fetchone()[0]
            revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            connection.close()

            self.assertIsNotNone(preserved_group)
            self.assertEqual(preserved_group[0], "Migrated signature group 1")
            self.assertTrue(preserved_group[1].startswith("compat-"))
            self.assertEqual(preserved_scope, [(1,)])
            self.assertEqual(other_generated_groups, 0)
            self.assertEqual(revision, ("0014_retire_signature_modes",))


if __name__ == "__main__":
    unittest.main()
