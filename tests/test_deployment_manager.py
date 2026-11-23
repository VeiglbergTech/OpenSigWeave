"""Deployment safety and optional-backup regression tests; no host Docker needed."""
from __future__ import annotations

from contextlib import nullcontext
from io import StringIO
from pathlib import Path
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import config_manager as cfg
import deploy_common as common
import install_manager as install
import update_manager as update


class Terminal:
    def __init__(self, interactive: bool):
        self.interactive = interactive

    def isatty(self):
        return self.interactive


def sample_env():
    return {
        "POSTGRES_DB": "opensigweave", "POSTGRES_USER": "opensigweave",
        "POSTGRES_PASSWORD": "p" * 45, "SECRET_KEY": "s" * 45,
        "ENGINE_API_KEY": "e" * 45, "APP_ALLOWED_HOSTS": "localhost",
        "APP_BIND_ADDRESS": "127.0.0.1", "APP_PORT": "8085",
        "LOCAL_ADMIN_EMAIL": "", "LOCAL_ADMIN_PASSWORD_HASH": "",
    }


def env_text(values):
    return "".join(f'{key}="{value}"\n' for key, value in values.items())


class ConfigTests(unittest.TestCase):
    def test_config_manager_standalone_script_resolves_auth_config(self):
        # Exercise the standalone import layout in an isolated Python process.
        script = (
            "import sys; "
            f"sys.path.insert(0, {str(ROOT / 'scripts')!r}); "
            "import config_manager; "
            "from auth_config import hash_local_admin_password, is_valid_email; "
            "assert is_valid_email('admin@example.com'); "
            "assert hash_local_admin_password('correct horse battery staple').startswith('scrypt')"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-c", script],
            cwd=ROOT, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_new_install_auto_generates_independent_secrets_and_scrypt(self):
        from auth_config import verify_local_admin_password
        with patch.object(cfg.sys, "stdin", Terminal(True)), \
             patch.object(cfg, "prompt", side_effect=["signature.example.test", "admin@example.test"]), \
             patch.object(cfg, "getpass", side_effect=["correct horse battery staple"] * 2), \
             patch.object(cfg, "_free_port", return_value=8085):
            values = cfg.create_values()
        keys = [values[k] for k in ("POSTGRES_PASSWORD", "SECRET_KEY",
                                      "ENGINE_API_KEY", "BACKUP_LOCATIONS_KEY")]
        self.assertEqual(len(set(keys)), 4)
        self.assertTrue(all(len(k) >= 32 for k in keys))
        from cryptography.fernet import Fernet
        Fernet(values["BACKUP_LOCATIONS_KEY"].encode("ascii"))
        self.assertTrue(verify_local_admin_password(
            "correct horse battery staple", values["LOCAL_ADMIN_PASSWORD_HASH"]
        ))
        self.assertNotIn("BACKUP_AGE_RECIPIENT", values)

    def test_local_defaults_and_headless_generated_admin_password(self):
        with patch.object(cfg.sys, "stdin", Terminal(False)), \
             patch.object(cfg, "_hostname_default", return_value="localhost"), \
             patch.object(cfg, "_free_port", return_value=8085):
            values = cfg.create_values(generate_admin_password=True)
        self.assertEqual(values["APP_ALLOWED_HOSTS"], "localhost")
        self.assertEqual(values["LOCAL_ADMIN_EMAIL"], "admin@localhost.localdomain")
        self.assertIn("_GENERATED_ADMIN_PASSWORD", values)

    def test_create_never_overwrites_and_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".env"
            with patch.object(cfg, "ENV", path):
                cfg._atomic_create('POSTGRES_USER="opensigweave"\n')
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                with self.assertRaises(common.DeploymentError):
                    cfg._atomic_create("evil")
                self.assertEqual(path.read_text(), 'POSTGRES_USER="opensigweave"\n')

    def test_repeated_generator_adds_key_without_rotating_other_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".env"
            initial = sample_env()
            path.write_text(env_text(initial), encoding="utf-8")
            os.chmod(path, 0o600)
            with patch.object(cfg, "ENV", path):
                cfg.migrate_config(preview=True)
                self.assertNotIn("BACKUP_LOCATIONS_KEY", common.parse_env(path))
                cfg.migrate_config()
                after = common.parse_env(path)
                for key in initial:
                    self.assertEqual(initial[key], after[key])
                self.assertIn("BACKUP_LOCATIONS_KEY", after)
                first = path.read_bytes()
                cfg.migrate_config()
                self.assertEqual(first, path.read_bytes())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_migrate_replaces_empty_backup_key_without_duplicate(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".env"
            initial = sample_env()
            initial["BACKUP_LOCATIONS_KEY"] = ""
            path.write_text(env_text(initial))
            os.chmod(path, 0o600)
            with patch.object(cfg, "ENV", path):
                cfg.migrate_config()
                parsed = common.parse_env(path)
                self.assertTrue(parsed["BACKUP_LOCATIONS_KEY"])
                self.assertEqual(path.read_text().count("BACKUP_LOCATIONS_KEY="), 1)
                cfg.migrate_config()
                self.assertEqual(common.parse_env(path), parsed)

    def test_check_does_not_modify_or_generate_missing_backup_key(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".env"
            path.write_text(env_text(sample_env()))
            os.chmod(path, 0o600)
            before = path.read_bytes()
            with patch.object(cfg, "ENV", path), \
                 patch.object(cfg, "exclusive_lock", return_value=nullcontext()):
                self.assertEqual(cfg.main(["--check"]), 0)
            self.assertEqual(before, path.read_bytes())

    def test_scrypt_hash_stays_literal_in_dotenv(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".env"
            values = sample_env() | {"DATABASE_URL": "postgresql://test",
                                     "SIGNATURE_STORAGE_PATH": "/data/signatures",
                                     "BACKUP_LOCATIONS_KEY": cfg._fernet_key()}
            values["LOCAL_ADMIN_EMAIL"] = "admin@example.test"
            values["LOCAL_ADMIN_PASSWORD_HASH"] = "scrypt$16384$8$1$abcdef$123456"
            with patch.object(cfg, "ENV", path), \
                 patch.object(cfg, "create_values", return_value=values):
                cfg.create_config()
            self.assertIn(
                "LOCAL_ADMIN_PASSWORD_HASH='scrypt$16384$8$1$abcdef$123456'",
                path.read_text(),
            )

    def test_existing_oidc_only_deployment_not_reset(self):
        values = sample_env()
        common.validate_config(values)
        self.assertNotIn("LOCAL_ADMIN_PASSWORD_HASH", cfg.missing_options(values))
        self.assertNotIn("POSTGRES_PASSWORD", cfg.missing_options(values))
        values["LOCAL_ADMIN_PASSWORD_HASH"] = "invalid"
        with self.assertRaises(common.DeploymentError):
            common.validate_config(values)

    def test_invalid_or_symlinked_config_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config"
            path.write_text("A=one\nA=two\n")
            with self.assertRaises(common.DeploymentError):
                common.parse_env(path)
            link = Path(temp) / "link"
            link.symlink_to(path)
            with self.assertRaises(common.DeploymentError):
                common.parse_env(link)
        with self.assertRaises(common.DeploymentError):
            common.validate_config({"SECRET_KEY": "REPLACE_WITH_ANYTHING"})


class InstallTests(unittest.TestCase):
    def test_port_conflict_detected(self):
        with patch.object(install.socket, "socket") as sockets:
            sockets.return_value.__enter__.return_value.bind.side_effect = OSError("occupied")
            self.assertFalse(install.port_available("127.0.0.1", 8085))

    def test_healthy_existing_installation_preserves_all_state(self):
        with patch.object(install, "exclusive_lock", return_value=nullcontext()), \
             patch.object(install, "pristine"), patch.object(install, "current_channel", return_value="dev"), \
             patch.object(install, "system_checks"), patch.object(install, "ENV", Mock(**{"exists.return_value": True,
                                                 "is_symlink.return_value": False})), \
             patch.object(install, "parse_env", return_value=sample_env()), \
             patch.object(install, "migrate_config"), \
             patch.object(install, "healthcheck"), \
             patch.object(install, "create_config") as create, \
             patch.object(install, "compose") as compose:
            install.perform_install(None)
            create.assert_not_called()
            compose.assert_not_called()


class UpdateTests(unittest.TestCase):
    def test_check_read_only_and_never_backups(self):
        with patch.object(update, "current_channel", return_value="dev"), \
             patch.object(update, "current_sha", return_value="a" * 40), \
             patch.object(update, "pristine"), \
             patch.object(update, "remote_sha", return_value="a" * 40), \
             patch.object(update, "git", return_value=""), \
             patch.object(update, "detect_backup") as backup, \
             patch.object(update, "compose") as compose:
            self.assertEqual(update.main(["--check"]), 0)
            backup.assert_not_called()
            compose.assert_not_called()

    def test_dry_run_without_backup_settings(self):
        with patch.object(update, "current_channel", return_value="dev"), \
             patch.object(update, "current_sha", return_value="a" * 40), \
             patch.object(update, "pristine"), \
             patch.object(update, "remote_sha", return_value="a" * 40), \
             patch.object(update, "preflight"), \
             patch.object(update, "validate_target", return_value=[]), \
             patch.object(update, "detect_backup") as backup:
            self.assertEqual(update.main(["--dry-run"]), 0)
            backup.assert_not_called()

    def test_no_backup_is_normal_and_never_warns(self):
        output = StringIO()
        with patch.object(update, "detect_backup", return_value={"status": "not_configured"}), \
             patch("sys.stdout", output), patch.object(update, "create_backup") as create:
            value = update.select_backup_for_update(yes=True, continue_without_backup=False)
        self.assertEqual(value["status"], "not_configured")
        self.assertNotIn("[WARN]", output.getvalue())
        create.assert_not_called()

    def test_configured_backup_verified_and_selected(self):
        metadata = {"status": "configured", "rule_id": "1" * 32,
                    "rule_name": "Daily", "location_name": "NAS"}
        verified = metadata | {"status": "verified", "file_key": "valid.age"}
        with patch.object(update, "detect_backup", return_value=metadata), \
             patch.object(update, "create_backup", return_value=verified) as create:
            self.assertEqual(
                update.select_backup_for_update(yes=True, continue_without_backup=False),
                verified,
            )
            create.assert_called_once()

    def test_backup_failure_requires_interactive_yes(self):
        with patch.object(update, "detect_backup", return_value={"status": "configured",
                 "rule_name": "Daily", "location_name": "NAS"}), \
             patch.object(update, "create_backup", side_effect=common.DeploymentError("failed", 7)), \
             patch.object(update.sys, "stdin", Terminal(True)), \
             patch("builtins.input", return_value="y"):
            result = update.select_backup_for_update(yes=True, continue_without_backup=False)
            self.assertEqual(result["status"], "failed_and_overridden")

    def test_backup_failure_declined_prevents_update(self):
        with patch.object(update, "detect_backup", return_value={"status": "configured",
                 "rule_name": "Daily", "location_name": "NAS"}), \
             patch.object(update, "create_backup", side_effect=common.DeploymentError("failed", 7)), \
             patch.object(update.sys, "stdin", Terminal(True)), \
             patch("builtins.input", return_value=""):
            with self.assertRaises(common.DeploymentError) as error:
                update.select_backup_for_update(yes=True, continue_without_backup=False)
            self.assertEqual(error.exception.code, 7)

    def test_backup_failure_noninteractive_yes_does_not_override(self):
        with patch.object(update, "detect_backup", return_value={"status": "configured",
                 "rule_name": "Daily", "location_name": "NAS"}), \
             patch.object(update, "create_backup", side_effect=common.DeploymentError("failed", 7)), \
             patch.object(update.sys, "stdin", Terminal(False)), \
             patch("builtins.input") as user_input:
            with self.assertRaises(common.DeploymentError):
                update.select_backup_for_update(yes=True, continue_without_backup=False)
            user_input.assert_not_called()

    def test_explicit_override_requires_yes(self):
        with patch.object(update, "current_channel") as channel:
            self.assertEqual(update.main(["--continue-without-backup"]), 4)
            channel.assert_not_called()

    def test_explicit_noninteractive_override_is_accepted(self):
        with patch.object(update, "detect_backup", side_effect=common.DeploymentError("docker down", 7)), \
             patch.object(update.sys, "stdin", Terminal(False)):
            result = update.select_backup_for_update(yes=True, continue_without_backup=True)
            self.assertEqual(result["status"], "failed_and_overridden")

    def test_interrupted_journal_without_backup_blocks_check(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = Path(temp) / "update-state.json"
            with patch.object(update, "UPDATE_JOURNAL", journal):
                update.write_update_journal(
                    channel="dev", installed="a" * 40, target="b" * 40,
                    backup={"status": "not_configured"},
                )
                self.assertEqual(stat.S_IMODE(journal.stat().st_mode), 0o600)
                record = json.loads(journal.read_text())
                self.assertEqual(record["backup"]["status"], "not_configured")
                with self.assertRaises(common.DeploymentError) as error:
                    update.ensure_no_incomplete_update()
                self.assertEqual(error.exception.code, 8)
                with patch.object(update, "current_channel", return_value="dev"), \
                     patch.object(update, "current_sha", return_value="b" * 40), \
                     patch.object(update, "remote_sha") as remote:
                    self.assertEqual(update.main(["--check"]), 8)
                    remote.assert_not_called()
                update.write_update_journal(
                    channel="dev", installed="a" * 40, target="b" * 40,
                    backup={"status": "failed_and_overridden"}, stage="migration",
                )
                self.assertEqual(json.loads(journal.read_text())["backup"]["status"],
                                 "failed_and_overridden")
                update.clear_update_journal()

    def test_first_upgrade_from_legacy_image_without_rules(self):
        with patch.object(update, "_backup_command", side_effect=common.DeploymentError("unknown CLI", 7)), \
             patch.object(update, "compose", return_value="") as compose:
            self.assertEqual(update.detect_backup()["status"], "not_configured")
            self.assertEqual(compose.call_count, 1)

    def test_legacy_image_with_enabled_rule_is_not_skipped(self):
        with patch.object(update, "_backup_command", side_effect=common.DeploymentError("unknown CLI", 7)), \
             patch.object(update, "compose", side_effect=["backup_rules", "1"]):
            with self.assertRaises(common.DeploymentError):
                update.detect_backup()

    def test_preupdate_cli_fail_closed_on_docker_errors(self):
        with patch.object(update, "compose", side_effect=common.DeploymentError("docker down", 3)):
            with self.assertRaises(common.DeploymentError) as error:
                update.detect_backup()
        self.assertEqual(error.exception.code, 7)

    def test_no_backup_update_writes_journal_and_migrates(self):
        calls = []
        def git(*args):
            if args[:2] == ("rev-parse", "refs/remotes/origin/dev"):
                return "b" * 40
            return ""
        def compose(*args, **kwargs):
            if "alembic" in args:
                return "0017_backup_rule_management"
            return ""
        with patch.object(update, "exclusive_lock", return_value=nullcontext()), \
             patch.object(update, "preflight", return_value=sample_env()), \
             patch.object(update, "current_sha", return_value="a" * 40), \
             patch.object(update, "current_channel", return_value="dev"), \
             patch.object(update, "git", side_effect=git), \
             patch.object(update, "validate_target", return_value=[]), \
             patch.object(update, "show_plan"), \
             patch.object(update, "select_backup_for_update", return_value={"status": "not_configured"}), \
             patch.object(update, "write_update_journal", side_effect=lambda **kw: calls.append(kw)), \
             patch.object(update, "clear_update_journal"), \
             patch.object(update, "parse_env", return_value=sample_env()), \
             patch.object(update, "validate_config"), \
             patch.object(update, "migrate_config"), \
             patch.object(update, "compose", side_effect=compose), \
             patch.object(update, "healthcheck"):
            update.apply_update("dev", "b" * 40, yes=True,
                                approve_breaking=False, skip_start=False)
        self.assertEqual([c["stage"] for c in calls],
                         ["source-switch", "migration", "migration-complete"])
        self.assertTrue(all(c["backup"]["status"] == "not_configured" for c in calls))

    def test_failure_before_backup_never_changes_git_or_volumes(self):
        with patch.object(update, "exclusive_lock", return_value=nullcontext()), \
             patch.object(update, "preflight", side_effect=common.DeploymentError("database down", 6)), \
             patch.object(update, "git") as git, patch.object(update, "compose") as compose:
            with self.assertRaises(common.DeploymentError):
                update.apply_update("dev", "b" * 40, yes=True,
                                    approve_breaking=False, skip_start=False)
            git.assert_not_called()
            compose.assert_not_called()

    def test_no_update_never_attempts_backup(self):
        with patch.object(update, "exclusive_lock", return_value=nullcontext()), \
             patch.object(update, "preflight", return_value={}), \
             patch.object(update, "current_sha", return_value="a" * 40), \
             patch.object(update, "select_backup_for_update") as backup:
            update.apply_update("dev", "a" * 40, yes=True,
                                approve_breaking=False, skip_start=False)
            backup.assert_not_called()

    def test_migration_failure_disallows_unsafe_rollback(self):
        self.assertIn("No verified recovery point", update.recovery_guidance(
            {"status": "failed_and_overridden"}
        ))
        self.assertIn("fresh-target restore", update.recovery_guidance(
            {"status": "verified", "file_key": "real.age", "location_name": "local"}
        ))


if __name__ == "__main__":
    unittest.main()
)"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-c", script],
            cwd=ROOT, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_new_install_auto_generates_independent_secrets_and_scrypt(self):
        from auth_config import verify_local_admin_password
        with patch.object(cfg.sys, "stdin", Terminal(True)), \
             patch.object(cfg, "prompt", side_effect=["signature.example.test", "admin@example.test"]), \
             patch.object(cfg, "getpass", side_effect=["correct horse battery staple"] * 2), \
             patch.object(cfg, "_free_port", return_value=8085):
            values = cfg.create_values()
        keys = [values[k] for k in ("POSTGRES_PASSWORD", "SECRET_KEY",
                                      "ENGINE_API_KEY", "BACKUP_LOCATIONS_KEY")]
        self.assertEqual(len(set(keys)), 4)
        self.assertTrue(all(len(k) >= 32 for k in keys))
        from cryptography.fernet import Fernet
        Fernet(values["BACKUP_LOCATIONS_KEY"].encode("ascii"))
        self.assertTrue(verify_local_admin_password(
            "correct horse battery staple", values["LOCAL_ADMIN_PASSWORD_HASH"]
        ))
        self.assertNotIn("BACKUP_AGE_RECIPIENT", values)

    def test_local_defaults_and_headless_generated_admin_password(self):
        with patch.object(cfg.sys, "stdin", Terminal(False)), \
             patch.object(cfg, "_hostname_default", return_value="localhost"), \
             patch.object(cfg, "_free_port", return_value=8085):
            values = cfg.create_values(generate_admin_password=True)
        self.assertEqual(values["APP_ALLOWED_HOSTS"], "localhost")
        self.assertEqual(values["LOCAL_ADMIN_EMAIL"], "admin@localhost.localdomain")
        self.assertIn("_GENERATED_ADMIN_PASSWORD", values)

    def test_create_never_overwrites_and_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".env"
            with patch.object(cfg, "ENV", path):
                cfg._atomic_create('POSTGRES_USER="opensigweave"\n')
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                with self.assertRaises(common.DeploymentError):
                    cfg._atomic_create("evil")
                self.assertEqual(path.read_text(), 'POSTGRES_USER="opensigweave"\n')

    def test_repeated_generator_adds_key_without_rotating_other_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".env"
            initial = sample_env()
            path.write_text(env_text(initial), encoding="utf-8")
            os.chmod(path, 0o600)
            with patch.object(cfg, "ENV", path):
                cfg.migrate_config(preview=True)
                self.assertNotIn("BACKUP_LOCATIONS_KEY", common.parse_env(path))
                cfg.migrate_config()
                after = common.parse_env(path)
                for key in initial:
                    self.assertEqual(initial[key], after[key])
                self.assertIn("BACKUP_LOCATIONS_KEY", after)
                first = path.read_bytes()
                cfg.migrate_config()
                self.assertEqual(first, path.read_bytes())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_migrate_replaces_empty_backup_key_without_duplicate(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".env"
            initial = sample_env()
            initial["BACKUP_LOCATIONS_KEY"] = ""
            path.write_text(env_text(initial))
            os.chmod(path, 0o600)
            with patch.object(cfg, "ENV", path):
                cfg.migrate_config()
                parsed = common.parse_env(path)
                self.assertTrue(parsed["BACKUP_LOCATIONS_KEY"])
                self.assertEqual(path.read_text().count("BACKUP_LOCATIONS_KEY="), 1)
                cfg.migrate_config()
                self.assertEqual(common.parse_env(path), parsed)

    def test_check_does_not_modify_or_generate_missing_backup_key(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".env"
            path.write_text(env_text(sample_env()))
            os.chmod(path, 0o600)
            before = path.read_bytes()
            with patch.object(cfg, "ENV", path), \
                 patch.object(cfg, "exclusive_lock", return_value=nullcontext()):
                self.assertEqual(cfg.main(["--check"]), 0)
            self.assertEqual(before, path.read_bytes())

    def test_scrypt_hash_stays_literal_in_dotenv(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".env"
            values = sample_env() | {"DATABASE_URL": "postgresql://test",
                                     "SIGNATURE_STORAGE_PATH": "/data/signatures",
                                     "BACKUP_LOCATIONS_KEY": cfg._fernet_key()}
            values["LOCAL_ADMIN_EMAIL"] = "admin@example.test"
            values["LOCAL_ADMIN_PASSWORD_HASH"] = "scrypt$16384$8$1$abcdef$123456"
            with patch.object(cfg, "ENV", path), \
                 patch.object(cfg, "create_values", return_value=values):
                cfg.create_config()
            self.assertIn(
                "LOCAL_ADMIN_PASSWORD_HASH='scrypt$16384$8$1$abcdef$123456'",
                path.read_text(),
            )

    def test_existing_oidc_only_deployment_not_reset(self):
        values = sample_env()
        common.validate_config(values)
        self.assertNotIn("LOCAL_ADMIN_PASSWORD_HASH", cfg.missing_options(values))
        self.assertNotIn("POSTGRES_PASSWORD", cfg.missing_options(values))
        values["LOCAL_ADMIN_PASSWORD_HASH"] = "invalid"
        with self.assertRaises(common.DeploymentError):
            common.validate_config(values)

    def test_invalid_or_symlinked_config_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config"
            path.write_text("A=one\nA=two\n")
            with self.assertRaises(common.DeploymentError):
                common.parse_env(path)
            link = Path(temp) / "link"
            link.symlink_to(path)
            with self.assertRaises(common.DeploymentError):
                common.parse_env(link)
        with self.assertRaises(common.DeploymentError):
            common.validate_config({"SECRET_KEY": "REPLACE_WITH_ANYTHING"})


class InstallTests(unittest.TestCase):
    def test_port_conflict_detected(self):
        with patch.object(install.socket, "socket") as sockets:
            sockets.return_value.__enter__.return_value.bind.side_effect = OSError("occupied")
            self.assertFalse(install.port_available("127.0.0.1", 8085))

    def test_healthy_existing_installation_preserves_all_state(self):
        with patch.object(install, "exclusive_lock", return_value=nullcontext()), \
             patch.object(install, "pristine"), patch.object(install, "current_channel", return_value="dev"), \
             patch.object(install, "system_checks"), patch.object(install, "ENV", Mock(**{"exists.return_value": True,
                                                 "is_symlink.return_value": False})), \
             patch.object(install, "parse_env", return_value=sample_env()), \
             patch.object(install, "migrate_config"), \
             patch.object(install, "healthcheck"), \
             patch.object(install, "create_config") as create, \
             patch.object(install, "compose") as compose:
            install.perform_install(None)
            create.assert_not_called()
            compose.assert_not_called()


class UpdateTests(unittest.TestCase):
    def test_check_read_only_and_never_backups(self):
        with patch.object(update, "current_channel", return_value="dev"), \
             patch.object(update, "current_sha", return_value="a" * 40), \
             patch.object(update, "pristine"), \
             patch.object(update, "remote_sha", return_value="a" * 40), \
             patch.object(update, "git", return_value=""), \
             patch.object(update, "detect_backup") as backup, \
             patch.object(update, "compose") as compose:
            self.assertEqual(update.main(["--check"]), 0)
            backup.assert_not_called()
            compose.assert_not_called()

    def test_dry_run_without_backup_settings(self):
        with patch.object(update, "current_channel", return_value="dev"), \
             patch.object(update, "current_sha", return_value="a" * 40), \
             patch.object(update, "pristine"), \
             patch.object(update, "remote_sha", return_value="a" * 40), \
             patch.object(update, "preflight"), \
             patch.object(update, "validate_target", return_value=[]), \
             patch.object(update, "detect_backup") as backup:
            self.assertEqual(update.main(["--dry-run"]), 0)
            backup.assert_not_called()

    def test_no_backup_is_normal_and_never_warns(self):
        output = StringIO()
        with patch.object(update, "detect_backup", return_value={"status": "not_configured"}), \
             patch("sys.stdout", output), patch.object(update, "create_backup") as create:
            value = update.select_backup_for_update(yes=True, continue_without_backup=False)
        self.assertEqual(value["status"], "not_configured")
        self.assertNotIn("[WARN]", output.getvalue())
        create.assert_not_called()

    def test_configured_backup_verified_and_selected(self):
        metadata = {"status": "configured", "rule_id": "1" * 32,
                    "rule_name": "Daily", "location_name": "NAS"}
        verified = metadata | {"status": "verified", "file_key": "valid.age"}
        with patch.object(update, "detect_backup", return_value=metadata), \
             patch.object(update, "create_backup", return_value=verified) as create:
            self.assertEqual(
                update.select_backup_for_update(yes=True, continue_without_backup=False),
                verified,
            )
            create.assert_called_once()

    def test_backup_failure_requires_interactive_yes(self):
        with patch.object(update, "detect_backup", return_value={"status": "configured",
                 "rule_name": "Daily", "location_name": "NAS"}), \
             patch.object(update, "create_backup", side_effect=common.DeploymentError("failed", 7)), \
             patch.object(update.sys, "stdin", Terminal(True)), \
             patch("builtins.input", return_value="y"):
            result = update.select_backup_for_update(yes=True, continue_without_backup=False)
            self.assertEqual(result["status"], "failed_and_overridden")

    def test_backup_failure_declined_prevents_update(self):
        with patch.object(update, "detect_backup", return_value={"status": "configured",
                 "rule_name": "Daily", "location_name": "NAS"}), \
             patch.object(update, "create_backup", side_effect=common.DeploymentError("failed", 7)), \
             patch.object(update.sys, "stdin", Terminal(True)), \
             patch("builtins.input", return_value=""):
            with self.assertRaises(common.DeploymentError) as error:
                update.select_backup_for_update(yes=True, continue_without_backup=False)
            self.assertEqual(error.exception.code, 7)

    def test_backup_failure_noninteractive_yes_does_not_override(self):
        with patch.object(update, "detect_backup", return_value={"status": "configured",
                 "rule_name": "Daily", "location_name": "NAS"}), \
             patch.object(update, "create_backup", side_effect=common.DeploymentError("failed", 7)), \
             patch.object(update.sys, "stdin", Terminal(False)), \
             patch("builtins.input") as user_input:
            with self.assertRaises(common.DeploymentError):
                update.select_backup_for_update(yes=True, continue_without_backup=False)
            user_input.assert_not_called()

    def test_explicit_override_requires_yes(self):
        with patch.object(update, "current_channel") as channel:
            self.assertEqual(update.main(["--continue-without-backup"]), 4)
            channel.assert_not_called()

    def test_explicit_noninteractive_override_is_accepted(self):
        with patch.object(update, "detect_backup", side_effect=common.DeploymentError("docker down", 7)), \
             patch.object(update.sys, "stdin", Terminal(False)):
            result = update.select_backup_for_update(yes=True, continue_without_backup=True)
            self.assertEqual(result["status"], "failed_and_overridden")

    def test_interrupted_journal_without_backup_blocks_check(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = Path(temp) / "update-state.json"
            with patch.object(update, "UPDATE_JOURNAL", journal):
                update.write_update_journal(
                    channel="dev", installed="a" * 40, target="b" * 40,
                    backup={"status": "not_configured"},
                )
                self.assertEqual(stat.S_IMODE(journal.stat().st_mode), 0o600)
                record = json.loads(journal.read_text())
                self.assertEqual(record["backup"]["status"], "not_configured")
                with self.assertRaises(common.DeploymentError) as error:
                    update.ensure_no_incomplete_update()
                self.assertEqual(error.exception.code, 8)
                with patch.object(update, "current_channel", return_value="dev"), \
                     patch.object(update, "current_sha", return_value="b" * 40), \
                     patch.object(update, "remote_sha") as remote:
                    self.assertEqual(update.main(["--check"]), 8)
                    remote.assert_not_called()
                update.write_update_journal(
                    channel="dev", installed="a" * 40, target="b" * 40,
                    backup={"status": "failed_and_overridden"}, stage="migration",
                )
                self.assertEqual(json.loads(journal.read_text())["backup"]["status"],
                                 "failed_and_overridden")
                update.clear_update_journal()

    def test_first_upgrade_from_legacy_image_without_rules(self):
        with patch.object(update, "_backup_command", side_effect=common.DeploymentError("unknown CLI", 7)), \
             patch.object(update, "compose", return_value="") as compose:
            self.assertEqual(update.detect_backup()["status"], "not_configured")
            self.assertEqual(compose.call_count, 1)

    def test_legacy_image_with_enabled_rule_is_not_skipped(self):
        with patch.object(update, "_backup_command", side_effect=common.DeploymentError("unknown CLI", 7)), \
             patch.object(update, "compose", side_effect=["backup_rules", "1"]):
            with self.assertRaises(common.DeploymentError):
                update.detect_backup()

    def test_preupdate_cli_fail_closed_on_docker_errors(self):
        with patch.object(update, "compose", side_effect=common.DeploymentError("docker down", 3)):
            with self.assertRaises(common.DeploymentError) as error:
                update.detect_backup()
        self.assertEqual(error.exception.code, 7)

    def test_no_backup_update_writes_journal_and_migrates(self):
        calls = []
        def git(*args):
            if args[:2] == ("rev-parse", "refs/remotes/origin/dev"):
                return "b" * 40
            return ""
        def compose(*args, **kwargs):
            if "alembic" in args:
                return "0017_backup_rule_management"
            return ""
        with patch.object(update, "exclusive_lock", return_value=nullcontext()), \
             patch.object(update, "preflight", return_value=sample_env()), \
             patch.object(update, "current_sha", return_value="a" * 40), \
             patch.object(update, "current_channel", return_value="dev"), \
             patch.object(update, "git", side_effect=git), \
             patch.object(update, "validate_target", return_value=[]), \
             patch.object(update, "show_plan"), \
             patch.object(update, "select_backup_for_update", return_value={"status": "not_configured"}), \
             patch.object(update, "write_update_journal", side_effect=lambda **kw: calls.append(kw)), \
             patch.object(update, "clear_update_journal"), \
             patch.object(update, "parse_env", return_value=sample_env()), \
             patch.object(update, "validate_config"), \
             patch.object(update, "migrate_config"), \
             patch.object(update, "compose", side_effect=compose), \
             patch.object(update, "healthcheck"):
            update.apply_update("dev", "b" * 40, yes=True,
                                approve_breaking=False, skip_start=False)
        self.assertEqual([c["stage"] for c in calls],
                         ["source-switch", "migration", "migration-complete"])
        self.assertTrue(all(c["backup"]["status"] == "not_configured" for c in calls))

    def test_failure_before_backup_never_changes_git_or_volumes(self):
        with patch.object(update, "exclusive_lock", return_value=nullcontext()), \
             patch.object(update, "preflight", side_effect=common.DeploymentError("database down", 6)), \
             patch.object(update, "git") as git, patch.object(update, "compose") as compose:
            with self.assertRaises(common.DeploymentError):
                update.apply_update("dev", "b" * 40, yes=True,
                                    approve_breaking=False, skip_start=False)
            git.assert_not_called()
            compose.assert_not_called()

    def test_no_update_never_attempts_backup(self):
        with patch.object(update, "exclusive_lock", return_value=nullcontext()), \
             patch.object(update, "preflight", return_value={}), \
             patch.object(update, "current_sha", return_value="a" * 40), \
             patch.object(update, "select_backup_for_update") as backup:
            update.apply_update("dev", "a" * 40, yes=True,
                                approve_breaking=False, skip_start=False)
            backup.assert_not_called()

    def test_migration_failure_disallows_unsafe_rollback(self):
        self.assertIn("No verified recovery point", update.recovery_guidance(
            {"status": "failed_and_overridden"}
        ))
        self.assertIn("fresh-target restore", update.recovery_guidance(
            {"status": "verified", "file_key": "real.age", "location_name": "local"}
        ))


if __name__ == "__main__":
    unittest.main()
