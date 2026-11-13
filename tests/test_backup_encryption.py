import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backup_archive import create_archive, write_manifest
from backup_encryption import (
    BackupEncryptionError,
    decrypt_and_verify_archive,
    decrypt_archive,
    encrypt_archive,
    encrypt_file,
)


class BackupEncryptionTests(unittest.TestCase):
    def _archive(self, root: Path) -> Path:
        stage = root / "stage"
        stage.mkdir()
        (stage / "payload.txt").write_text("backup payload", encoding="utf-8")
        write_manifest(stage, {"application_version": "test"})
        archive = root / "backup.tar.gz"
        create_archive(stage, archive)
        return archive

    def _mock_age(self, arguments, **kwargs):
        output = Path(kwargs["output_path"])
        source = Path(arguments[-1])
        shutil.copyfile(source, output)

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    def test_encrypt_and_verify_with_age(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self._archive(root)
            encrypted = root / "backup.tar.gz.age"
            identity = root / "age.identity"
            identity.write_text("AGE-SECRET-KEY-1TEST", encoding="utf-8")
            with patch("backup_encryption.subprocess.run", side_effect=self._mock_age):
                encrypt_archive(archive, encrypted, "age1example")
                manifest = decrypt_and_verify_archive(encrypted, identity)
            self.assertEqual(manifest["metadata"]["application_version"], "test")
            self.assertTrue(encrypted.exists())

    def test_decrypt_publishes_only_verified_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self._archive(root)
            encrypted = root / "backup.age"
            shutil.copyfile(archive, encrypted)
            identity = root / "age.identity"
            identity.write_text("AGE-SECRET-KEY-1TEST", encoding="utf-8")
            output = root / "decrypted.tar.gz"
            with patch("backup_encryption.subprocess.run", side_effect=self._mock_age):
                decrypt_archive(encrypted, identity, output)
            self.assertTrue(output.exists())
            self.assertEqual(output.read_bytes(), archive.read_bytes())

    def test_encrypt_file_publishes_only_ciphertext_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / ".env"
            source.write_text("SECRET=value", encoding="utf-8")
            encrypted = root / "env.enc"
            with patch("backup_encryption.subprocess.run", side_effect=self._mock_age):
                encrypt_file(source, encrypted, "age1example")
            self.assertEqual(encrypted.read_text(encoding="utf-8"), "SECRET=value")

    def test_missing_recipient_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self._archive(root)
            with self.assertRaises(BackupEncryptionError):
                encrypt_archive(archive, root / "backup.age", " ")

    def test_existing_destination_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self._archive(root)
            encrypted = root / "backup.age"
            encrypted.write_text("keep", encoding="utf-8")
            with self.assertRaises(BackupEncryptionError):
                encrypt_archive(archive, encrypted, "age1example")
            self.assertEqual(encrypted.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
