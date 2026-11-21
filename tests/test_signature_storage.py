import hashlib
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

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
            # Deliberately create a permissive fixture to verify the store hardens it.
            os.chmod(directory, 0o755)  # nosec B103
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

    def test_signature_images_support_png_jpeg_webp_and_content_addressing(self):
        fixtures = (
            ("image/png", b"\x89PNG\r\n\x1a\nfixture", ".png"),
            ("image/jpeg", b"\xff\xd8\xfffixture", ".jpg"),
            ("image/webp", b"RIFF\x08\x00\x00\x00WEBPfixture", ".webp"),
        )
        with tempfile.TemporaryDirectory() as directory:
            store = SignatureFileStore(directory)
            for content_type, payload, extension in fixtures:
                with self.subTest(content_type=content_type):
                    filename = store.write_image(payload, content_type)
                    self.assertTrue(filename.endswith(extension))
                    self.assertEqual(filename, store.write_image(payload, content_type))
                    self.assertEqual(store.image_path(filename).read_bytes(), payload)
                    self.assertEqual(store.image_media_type(filename), content_type)

    def test_identical_concurrent_image_upload_reuses_completed_asset(self):
        # Model another upload winning the exclusive filesystem link race.
        # The losing upload must return the same asset URL, not a 422 error.
        with tempfile.TemporaryDirectory() as directory:
            store = SignatureFileStore(directory)
            payload = b"\x89PNG\r\n\x1a\nconcurrent"
            original_write = store._atomic_write

            def competing_write(destination, content, *, exclusive=False):
                original_write(destination, content, exclusive=exclusive)
                raise SignatureStorageError("Signature version already exists")

            with patch.object(store, "_atomic_write", side_effect=competing_write):
                filename = store.write_image(payload, "image/png")
            self.assertEqual(store.image_path(filename).read_bytes(), payload)
            self.assertEqual(store.write_image(payload, "image/png"), filename)

            # A genuine disk error without a concurrent winner must still fail.
            other_payload = b"\x89PNG\r\n\x1a\nfailed"
            with patch.object(store, "_atomic_write",
                              side_effect=SignatureStorageError("Disk write failed")):
                with self.assertRaises(SignatureStorageError):
                    store.write_image(other_payload, "image/png")

    def test_signature_image_mime_spoofing_and_unsafe_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SignatureFileStore(directory)
            with self.assertRaises(SignatureStorageError):
                store.write_image(b"GIF89a-not-a-png", "image/png")
            with self.assertRaises(SignatureStorageError):
                store.write_image(b"RIFF\x08\x00\x00\x00NOPEfixture", "image/webp")
            with self.assertRaises(SignatureStorageError):
                store.write_image(b"\x89PNG\r\n\x1a\nfixture", "text/plain")
            for unsafe in ("../image.png", "a" * 64 + ".svg", "A" * 64 + ".png"):
                with self.subTest(filename=unsafe):
                    with self.assertRaises(SignatureStorageError):
                        store.image_path(unsafe)

    def test_deleting_signature_html_does_not_delete_shared_image_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SignatureFileStore(directory)
            image_name = store.write_image(b"\x89PNG\r\n\x1a\nshared", "image/png")
            version_id = uuid.uuid4()
            stored = store.write(
                version_id,
                "Brand",
                f'<img src="https://signature.example.org/signature-assets/{image_name}">',
            )
            self.assertIn(image_name, store.read("Brand", version_id, stored.sha256))
            store.delete("Brand", version_id)
            self.assertEqual(store.image_path(image_name).read_bytes(), b"\x89PNG\r\n\x1a\nshared")

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
