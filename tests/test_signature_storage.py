import hashlib
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from signature_storage import SignatureFileStore, SignatureStorageError


class SignatureFileStoreTests(unittest.TestCase):
    def test_write_and_read_use_name_based_directory_and_canonical_file(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SignatureFileStore(directory)
            version_id = uuid.uuid4()
            content = "<table><tr><td>Signature</td></tr></table>"
            stored = store.write(version_id, "Default signature", content)
            expected = f"signatur/Default signature/.versions/{version_id.hex}.html"
            self.assertEqual(stored.relative_path, expected)
            self.assertEqual(stored.sha256, hashlib.sha256(content.encode()).hexdigest())
            self.assertEqual(stored.size_bytes, len(content.encode()))
            self.assertEqual(store.read("Default signature", version_id, stored.sha256), content)
            canonical = Path(directory) / "signatur" / "Default signature" / "signature.html"
            self.assertEqual(canonical.read_text(encoding="utf-8"), content)
            self.assertEqual(canonical.stat().st_mode & 0o777, 0o600)

    def test_new_versions_preserve_snapshots_and_update_canonical_file(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SignatureFileStore(directory)
            first_id, second_id = uuid.uuid4(), uuid.uuid4()
            first = store.write(first_id, "Brand", "first")
            second = store.write(second_id, "Brand", "second")
            self.assertEqual(store.read("Brand", first_id, first.sha256), "first")
            self.assertEqual(store.read("Brand", second_id, second.sha256), "second")
            self.assertEqual((Path(directory) / "signatur" / "Brand" / "signature.html").read_text(), "second")

    def test_oversized_signature_asset_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SignatureFileStore(directory)
            with self.assertRaises(SignatureStorageError):
                store.write(uuid.uuid4(), "Brand", "x" * 65537)

    def test_existing_version_id_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SignatureFileStore(directory)
            version_id = uuid.uuid4()
            store.write(version_id, "Brand", "first")
            with self.assertRaises(SignatureStorageError):
                store.write(version_id, "Brand", "second")
            self.assertEqual(store.read("Brand", version_id, hashlib.sha256(b"first").hexdigest()), "first")

    def test_tampered_content_fails_integrity_check(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SignatureFileStore(directory)
            version_id = uuid.uuid4()
            stored = store.write(version_id, "Brand", "original")
            snapshot = Path(directory) / stored.relative_path
            snapshot.write_text("tampered", encoding="utf-8")
            with self.assertRaises(SignatureStorageError):
                store.read("Brand", version_id, stored.sha256)

    def test_symlinked_signature_asset_is_rejected_for_read_and_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SignatureFileStore(directory)
            target_id, linked_id = uuid.uuid4(), uuid.uuid4()
            target = store.write(target_id, "Brand", "trusted")
            versions = Path(directory) / "signatur" / "Brand" / ".versions"
            (versions / f"{linked_id.hex}.html").symlink_to(versions / f"{target_id.hex}.html")
            with self.assertRaises(SignatureStorageError):
                store.read("Brand", linked_id, target.sha256)
            with self.assertRaises(SignatureStorageError):
                store.delete("Brand", linked_id)
            self.assertEqual(store.read("Brand", target_id, target.sha256), "trusted")

    def test_storage_root_permissions_are_restricted(self):
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o755)
            store = SignatureFileStore(directory)
            self.assertEqual(store.root.stat().st_mode & 0o777, 0o700)

    def test_version_identifier_and_signature_name_are_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SignatureFileStore(directory)
            with self.assertRaises(TypeError):
                store.write("../outside", "Brand", "unsafe")
            for name in ("../outside", "a/b", "a\\b", ".."):
                with self.assertRaises(SignatureStorageError):
                    store.write(uuid.uuid4(), name, "unsafe")

    def test_signature_directory_can_be_renamed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SignatureFileStore(directory)
            version_id = uuid.uuid4()
            stored = store.write(version_id, "Old name", "content")
            store.rename_signature("Old name", "New name")
            self.assertEqual(store.read("New name", version_id, stored.sha256), "content")
            self.assertTrue((Path(directory) / "signatur" / "New name" / "signature.html").exists())
            self.assertFalse((Path(directory) / "signatur" / "Old name").exists())


if __name__ == "__main__":
    unittest.main()
