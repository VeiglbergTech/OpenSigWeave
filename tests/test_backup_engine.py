"""Container backup orchestration tests; external PostgreSQL and age are stubbed.

Existing test_backup_encryption exercises real age cryptography in CI, and
test_backup_rules covers Local/SMB destination publication and retention.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import backup_engine


class ContainerBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("backups", "signatures", "idp"):
            (self.root / name).mkdir(mode=0o700)
        (self.root / "signatures" / "signature.html").write_text("<p>Signature</p>")
        (self.root / "idp" / "provider.json").write_text('{"token":"private"}')
        self.env = patch.dict(os.environ, {
            "DATABASE_URL": "postgresql+psycopg://app:secret@postgres:5432/opensigweave",
            "BACKUP_WORK_ROOT": str(self.root / "backups"),
            "SIGNATURE_STORAGE_PATH": str(self.root / "signatures"),
            "IDP_CONFIG_DIR": str(self.root / "idp"),
            "BACKUP_AGE_RECIPIENT": "age1test-recipient-for-unit-test",
            "BACKUP_AGE_IDENTITY": "AGE-SECRET-KEY-TEST-ONLY",
            "BACKUP_AGE_IDENTITY_FILE": "",
            "UVICORN_WORKERS": "1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def fake_pg(self, argv, **kwargs):
        self.assertEqual(kwargs["env"]["PGPASSWORD"], "secret")
        self.assertNotIn("secret", " ".join(argv))
        if argv[0] == "psql":
            return subprocess.CompletedProcess(argv, 0, b"0017_backup_rule_management\n", b"")
        if argv[0] == "pg_dump":
            output = Path(argv[argv.index("--file") + 1])
            output.write_bytes(b"PGDMP\x00test database")
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        self.fail("Unexpected PostgreSQL command")

    def fake_encrypt(self, source, target, recipient):
        self.assertTrue(recipient.startswith("age1"))
        shutil.copyfile(source, target)

    def fake_decrypt(self, source, identity, target):
        self.assertEqual(identity.read_text().strip(), "AGE-SECRET-KEY-TEST-ONLY")
        shutil.copyfile(source, target)

    def create(self):
        result = {}
        def deliver(path, size, digest):
            destination = self.root / "output.age"
            shutil.copyfile(path, destination)
            result["size"] = size
            result["digest"] = digest
        with patch.object(backup_engine.subprocess, "run", side_effect=self.fake_pg), \
             patch.object(backup_engine, "encrypt_archive", side_effect=self.fake_encrypt), \
             patch.object(backup_engine, "decrypt_archive", side_effect=self.fake_decrypt):
            actual = backup_engine.create_application_backup(deliver)
        return actual, result

    def test_application_snapshot_contains_database_and_persistent_files(self):
        actual, result = self.create()
        self.assertEqual(actual, (result["size"], result["digest"]))
        self.assertGreater(result["size"], 0)
        restored = self.root / "restored"
        # The on-disk stub is plaintext to allow manifest inspection; production
        # archive encryption/decryption is tested separately.
        from backup_archive import extract_archive
        payload = self.root / "output.age"
        extracted = extract_archive(payload, restored)
        self.assertEqual(extracted["metadata"]["backup_kind"], "application")
        self.assertEqual((restored / "data/signatures/signature.html").read_text(), "<p>Signature</p>")
        self.assertEqual((restored / "data/idp-config/provider.json").read_text(), '{"token":"private"}')
        self.assertEqual((restored / "database/opensigweave.dump").read_bytes(), b"PGDMP\x00test database")
        self.assertFalse((restored / "age-identity.txt").exists())
        self.assertEqual(list((self.root / "backups").iterdir()), [])

    def test_rejects_symlinked_signature_data(self):
        (self.root / "signatures" / "unsafe").symlink_to(self.root / "idp" / "provider.json")
        with patch.object(backup_engine.subprocess, "run", side_effect=self.fake_pg):
            with self.assertRaisesRegex(backup_engine.BackupEngineError, "symbolic link"):
                backup_engine.create_application_backup(lambda *_: None)
        self.assertEqual(list((self.root / "backups").iterdir()), [])

    def test_no_encryption_without_identity_or_recipient(self):
        with patch.dict(os.environ, {"BACKUP_AGE_IDENTITY": "", "BACKUP_AGE_IDENTITY_FILE": ""}):
            with self.assertRaisesRegex(backup_engine.BackupEngineError, "identity"):
                backup_engine.create_application_backup(lambda *_: None)
        with patch.dict(os.environ, {"BACKUP_AGE_RECIPIENT": ""}):
            with self.assertRaisesRegex(backup_engine.BackupEngineError, "RECIPIENT"):
                backup_engine.create_application_backup(lambda *_: None)

    def test_never_overwrite_existing_restore_target(self):
        target = self.root / "existing"
        target.mkdir()
        with self.assertRaisesRegex(backup_engine.BackupEngineError, "existing directory"):
            backup_engine.restore_application_to_new_directory(
                self.root / "missing.age", self.root / "missing.key", target,
            )

    def test_no_multiprocess_snapshot_without_shared_gate(self):
        with patch.dict(os.environ, {"UVICORN_WORKERS": "2"}):
            with self.assertRaisesRegex(backup_engine.BackupEngineError, "UVICORN_WORKERS"):
                backup_engine.create_application_backup(lambda *_: None)


if __name__ == "__main__":
    unittest.main()
