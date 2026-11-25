import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backup_encryption import BackupEncryptionError
from backup_retention import BackupRetentionError, prune_backups


class BackupRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.identity = self.root / "identity.txt"
        self.identity.write_text("age-secret-identity", encoding="utf-8")
        self.backups = self.root / "backups"
        self.backups.mkdir()
        for name in ("one.age", "two.age", "three.age"):
            (self.backups / name).write_bytes(b"ciphertext-" + name.encode())

    def tearDown(self):
        self.temporary.cleanup()

    @patch("backup_retention.decrypt_and_verify_archive")
    def test_dry_run_never_deletes_and_keeps_newest(self, verify):
        result = prune_backups(self.backups, self.identity, keep=2)
        self.assertEqual(len(verify.call_args_list), 3)
        self.assertEqual(len(result["retained"]), 2)
        self.assertEqual(len(result["would_delete"]), 1)
        self.assertEqual(result["deleted"], [])
        self.assertEqual(len(list(self.backups.glob("*.age"))), 3)

    @patch("backup_retention.decrypt_and_verify_archive")
    def test_apply_deletes_only_after_all_archives_verify(self, verify):
        result = prune_backups(self.backups, self.identity, keep=1, apply=True)
        self.assertEqual(len(result["deleted"]), 2)
        self.assertEqual(len(list(self.backups.glob("*.age"))), 1)

    @patch("backup_retention.decrypt_and_verify_archive")
    def test_invalid_archive_aborts_without_deleting_anything(self, verify):
        verify.side_effect = BackupEncryptionError("invalid")
        with self.assertRaises(BackupRetentionError):
            prune_backups(self.backups, self.identity, keep=1, apply=True)
        self.assertEqual(len(list(self.backups.glob("*.age"))), 3)

    def test_keep_must_be_positive(self):
        with self.assertRaises(BackupRetentionError):
            prune_backups(self.backups, self.identity, keep=0)

    def test_symlinked_backup_directory_is_rejected(self):
        link = self.root / "linked-backups"
        link.symlink_to(self.backups, target_is_directory=True)
        with self.assertRaises(BackupRetentionError):
            prune_backups(link, self.identity)


if __name__ == "__main__":
    unittest.main()
