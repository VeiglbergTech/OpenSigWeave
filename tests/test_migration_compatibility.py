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
                "SELECT domain_name, template_html FROM domains WHERE id = 1"
            ).fetchone()
            override = connection.execute(
                "SELECT user_email, html_content FROM user_overrides WHERE id = 1"
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

            self.assertEqual(domain, ("example.org", "<p>Keep me</p>"))
            self.assertEqual(override, ("alice@example.org", "<p>Existing</p>"))
            self.assertEqual(revision, ("0011_legacy_signature_groups",))
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
            self.assertIn("identity_provider_id", columns)
            self.assertIn("authentication_provider_id", columns)

            self.assertTrue({"group_id", "signature_id", "weight", "is_enabled"}.issubset(group_member_columns))
            self.assertIn("group_id", rule_columns)
            cache_columns = {row[1] for row in connection.execute("PRAGMA table_info(identity_attribute_cache)")}
            self.assertIn("principal_id", cache_columns)
            legacy_groups = connection.execute("SELECT slug FROM signature_groups ORDER BY slug").fetchall()
            self.assertEqual(legacy_groups, [("legacy-auto",), ("legacy-internal",), ("legacy-professional",)])
            connection.close()


if __name__ == "__main__":
    unittest.main()
