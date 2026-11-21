import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backup_cli


class BackupCliTests(unittest.TestCase):
    def test_v1_operator_cli_exposes_only_existing_backup_commands(self):
        import argparse

        parser = backup_cli.build_parser()
        command_actions = [
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ]
        self.assertEqual(len(command_actions), 1)
        self.assertEqual(
            set(command_actions[0].choices),
            {"create", "restore", "package", "verify", "decrypt", "prune"},
        )

    def test_backup_prune_is_dry_run_by_default(self):
        args = backup_cli.build_parser().parse_args([
            "prune", "--directory", "/backup", "--identity", "/keys/backup.txt",
        ])
        self.assertFalse(args.apply)
        args = backup_cli.build_parser().parse_args([
            "prune", "--directory", "/backup", "--identity", "/keys/backup.txt", "--apply",
        ])
        self.assertTrue(args.apply)

    def test_package_requires_existing_staging_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing"
            result = backup_cli.main([
                "package",
                "--staging", str(missing),
                "--output", str(Path(temporary) / "backup.age"),
                "--recipient", "age1example",
            ])
            self.assertEqual(result, 2)

    def test_verify_reports_file_count(self):
        with patch(
            "backup_cli.decrypt_and_verify_archive",
            return_value={
                "format": "opensigweave-backup",
                "format_version": 1,
                "files": [{"path": "data/file", "size_bytes": 1, "sha256": "0" * 64}],
            },
        ):
            with tempfile.TemporaryDirectory() as temporary:
                backup = Path(temporary) / "backup.age"
                backup.write_bytes(b"ciphertext")
                identity = Path(temporary) / "identity"
                identity.write_text("identity", encoding="utf-8")
                self.assertEqual(
                    backup_cli.main(["verify", str(backup), "--identity", str(identity)]),
                    0,
                )


if __name__ == "__main__":
    unittest.main()
