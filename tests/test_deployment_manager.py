"""Deployment safety regression tests. No production Docker or network required."""
from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
import os
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import config_manager as cfg
import deploy_common as common
import install_manager as install
import update_manager as update


class ConfigTests(unittest.TestCase):
    def test_crypto_and_scrypt(self):
        from auth_config import verify_local_admin_password
        with patch.object(cfg, "prompt", side_effect=[
            "signature.example.test", "127.0.0.1", "8085", "admin@example.test"
        ]), patch.object(cfg, "getpass", side_effect=["correct horse battery staple"] * 2):
            values = cfg.create_values()
        secrets = [values[x] for x in ("POSTGRES_PASSWORD", "SECRET_KEY", "ENGINE_API_KEY")]
        self.assertEqual(len(set(secrets)), 3)
        self.assertTrue(all(len(x) >= 32 for x in secrets))
        self.assertTrue(verify_local_admin_password(
            "correct horse battery staple", values["LOCAL_ADMIN_PASSWORD_HASH"]
        ))

    def test_create_does_not_overwrite_and_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".env"
            with patch.object(cfg, "ENV", path):
                cfg._atomic_create('POSTGRES_USER="opensigweave"\n')
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                with self.assertRaises(common.DeploymentError):
                    cfg._atomic_create("evil")
                self.assertEqual(path.read_text(), 'POSTGRES_USER="opensigweave"\n')

    def test_preview_does_not_write_and_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".env"
            with patch.object(cfg, "ENV", path), patch.object(cfg, "validate_config"), \
                 patch.object(cfg, "missing_options", side_effect=lambda env: (
                     {"APP_PORT": "8085"} if "APP_PORT" not in env else {}
                 )):
                path.write_text('SECRET_KEY="preserve_me"\n')
                os.chmod(path, 0o600)
                before = path.read_bytes()
                cfg.migrate_config(preview=True)
                self.assertEqual(before, path.read_bytes())
                cfg.migrate_config(yes=True)
                once = path.read_bytes()
                self.assertIn(b'APP_PORT="8085"', once)
                self.assertIn(before, once)
                cfg.migrate_config(yes=True)
                self.assertEqual(once, path.read_bytes())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_scrypt_hash_written_with_literal_dollar_separators(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".env"
            values = {key: "testing" for key in (
                "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "DATABASE_URL",
                "SIGNATURE_STORAGE_PATH", "APP_BIND_ADDRESS", "APP_PORT",
                "SECRET_KEY", "ENGINE_API_KEY", "APP_ALLOWED_HOSTS",
                "LOCAL_ADMIN_EMAIL", "LOCAL_ADMIN_PASSWORD_HASH"
            )}
            values["LOCAL_ADMIN_PASSWORD_HASH"] = "scrypt$16384$8$1$abcdef$123456"
            with patch.object(cfg, "ENV", path), patch.object(cfg, "create_values", return_value=values):
                cfg.create_config()
            self.assertIn(
                "LOCAL_ADMIN_PASSWORD_HASH='scrypt$16384$8$1$abcdef$123456'",
                path.read_text()
            )

    def test_refuses_duplicate_and_symlink_env(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "env"
            path.write_text("A=one\nA=two\n")
            os.chmod(path, 0o600)
            with self.assertRaises(common.DeploymentError):
                common.parse_env(path)
            link = Path(temp) / "link"
            link.symlink_to(path)
            with self.assertRaises(common.DeploymentError):
                common.parse_env(link)

    def test_invalid_config_rejected(self):
        with self.assertRaises(common.DeploymentError):
            common.validate_config({"SECRET_KEY": "REPLACE_WITH_ANYTHING"})


class UpdateTests(unittest.TestCase):
    def test_unsupported_channel_aborts(self):
        with patch.object(update, "current_channel", side_effect=common.DeploymentError("detached", 4)):
            self.assertEqual(update.main(["--version"]), 4)

    def test_check_read_only(self):
        with patch.object(update, "current_channel", return_value="dev"), \
             patch.object(update, "current_sha", return_value="a" * 40), \
             patch.object(update, "pristine"), \
             patch.object(update, "remote_sha", return_value="a" * 40), \
             patch.object(update, "git", return_value=""), \
             patch.object(update, "create_backup") as backup, \
             patch.object(update, "compose") as compose:
            self.assertEqual(update.main(["--check"]), 0)
            backup.assert_not_called()
            compose.assert_not_called()

    def test_no_update_never_backs_up(self):
        with patch.object(update, "exclusive_lock", return_value=nullcontext()), \
             patch.object(update, "preflight", return_value={}), \
             patch.object(update, "current_sha", return_value="a" * 40), \
             patch.object(update, "create_backup") as backup:
            update.apply_update("dev", "a" * 40, yes=True, approve_breaking=False, skip_start=False)
            backup.assert_not_called()

    def test_backup_failure_prevents_merge_and_migration(self):
        with patch.object(update, "exclusive_lock", return_value=nullcontext()), \
             patch.object(update, "preflight", return_value={}), \
             patch.object(update, "current_sha", return_value="a" * 40), \
             patch.object(update, "git", side_effect=lambda *args: {
                 ("rev-parse", "refs/remotes/origin/dev"): "b" * 40,
             }.get(args, "")) as git, \
             patch.object(update, "validate_target", return_value=[]), \
             patch.object(update, "show_plan"), \
             patch.object(update, "backup_options"), \
             patch.object(update, "create_backup",
                          side_effect=common.DeploymentError("verification failed", 7)), \
             patch.object(update, "compose") as compose:
            with self.assertRaises(common.DeploymentError):
                update.apply_update("dev", "b" * 40, yes=True,
                                    approve_breaking=False, skip_start=False)
            self.assertFalse(any(args.args[0] == "merge" for args in git.call_args_list))
            compose.assert_not_called()

    def test_critical_paths_require_explicit_approval(self):
        with patch.object(update, "exclusive_lock", return_value=nullcontext()), \
             patch.object(update, "preflight", return_value={}), \
             patch.object(update, "current_sha", return_value="a" * 40), \
             patch.object(update, "git", side_effect=lambda *args: (
                 "b" * 40 if args[0] == "rev-parse" else ""
             )), \
             patch.object(update, "validate_target", return_value=["migrations/versions/new.py"]), \
             patch.object(update, "show_plan"), \
             patch.object(update, "create_backup") as backup:
            with self.assertRaises(common.DeploymentError):
                update.apply_update("dev", "b" * 40, yes=True,
                                    approve_breaking=False, skip_start=False)
            backup.assert_not_called()

    def test_offline_identity_is_mandatory(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing.env"
            with patch.object(update, "BACKUP_CONFIG", missing):
                with self.assertRaises(common.DeploymentError):
                    update.backup_options()

    def test_port_conflict_detected(self):
        with patch.object(install.socket, "socket") as socket_class:
            socket_class.return_value.__enter__.return_value.bind.side_effect = OSError("occupied")
            self.assertFalse(install.port_available("127.0.0.1", 8085))


if __name__ == "__main__":
    unittest.main()
