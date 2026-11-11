import hashlib
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from signature_storage import SignatureFileStore, SignatureStorageError


class SignatureFileStoreTests(unittest.TestCase):
    def test_write_and_read_preserve_content_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SignatureFileStore(directory)
            version_id = uuid.uuid4()
            content = "<table><tr><td>Signature</td></tr></table>"
            stored = store.write(version_id, content)

            self.assertEqual(stored.relative_path, f"{version_id.hex}.html")
            self.assertEqual(stored.sha256, hashlib.sha256(content.encode()).hexdigest())
            self.assertEqual(stored.size_bytes, len(content.encode()))
            self.assertEqual(store.read(version_id, stored.sha256), content)
            self.assertEqual((Path(directory) / stored.relative_path).stat().st_mode & 0o777, 0o600)

    def test_existing_version_id_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SignatureFileStore(directory)
            version_id = uuid.uuid4()
            store.write(version_id, "first")
            with self.assertRaises(SignatureStorageError):
                store.write(version_id, "second")
            self.assertEqual(store.read(version_id, hashlib.sha256(b"first").hexdigest()), "first")

    def test_tampered_content_fails_integrity_check(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SignatureFileStore(directory)
            version_id = uuid.uuid4()
            stored = store.write(version_id, "original")
            (Path(directory) / stored.relative_path).write_text("tampered", encoding="utf-8")
            with self.assertRaises(SignatureStorageError):
                store.read(version_id, stored.sha256)


    def test_symlinked_signature_asset_is_rejected_for_read_and_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SignatureFileStore(directory)
            target_id = uuid.uuid4()
            target = store.write(target_id, "trusted")
            linked_id = uuid.uuid4()
            link_path = Path(directory) / f"{linked_id.hex}.html"
            link_path.symlink_to(Path(directory) / target.relative_path)

            with self.assertRaises(SignatureStorageError):
                store.read(linked_id, target.sha256)
            with self.assertRaises(SignatureStorageError):
                store.delete(linked_id)
            self.assertEqual(store.read(target_id, target.sha256), "trusted")

    def test_storage_root_permissions_are_restricted(self):
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o755)
            store = SignatureFileStore(directory)
            self.assertEqual(store.root.stat().st_mode & 0o777, 0o700)

    def test_version_identifier_must_be_uuid(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SignatureFileStore(directory)
            with self.assertRaises(TypeError):
                store.write("../outside", "unsafe")


if __name__ == "__main__":
    unittest.main()
