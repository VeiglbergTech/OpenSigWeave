import hashlib
import json
import tempfile
import tarfile
import unittest
from pathlib import Path

from backup_archive import (
    BackupArchiveError,
    create_archive,
    extract_archive,
    verify_archive,
    write_manifest,
)


class BackupArchiveTests(unittest.TestCase):
    def _make_archive(self, root: Path, archive_path: Path) -> None:
        (root / "database").mkdir()
        (root / "database" / "opensigweave.dump").write_bytes(b"database snapshot")
        (root / "deployment").mkdir()
        (root / "deployment" / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
        write_manifest(root, {"application_version": "test"})
        create_archive(root, archive_path)

    def test_archive_round_trip_and_manifest_checksums(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "stage"
            source.mkdir()
            archive = base / "backup.tar.gz"
            self._make_archive(source, archive)
            manifest = verify_archive(archive)
            self.assertEqual(manifest["format"], "opensigweave-backup")
            self.assertEqual(manifest["metadata"]["application_version"], "test")
            restored = base / "restored"
            extract_archive(archive, restored)
            self.assertEqual((restored / "database" / "opensigweave.dump").read_bytes(), b"database snapshot")
            self.assertEqual((restored / "deployment" / "compose.yaml").read_text(), "services: {}\n")

    def test_staging_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "outside").write_text("secret")
            (root / "linked").symlink_to(root / "outside")
            with self.assertRaises(BackupArchiveError):
                write_manifest(root)

    def test_tampered_payload_fails_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "stage"
            source.mkdir()
            archive = base / "backup.tar.gz"
            self._make_archive(source, archive)
            tampered = base / "tampered.tar.gz"
            with tarfile.open(archive, "r:gz") as original, tarfile.open(tampered, "w:gz") as changed:
                for member in original.getmembers():
                    stream = original.extractfile(member)
                    data = stream.read() if stream else b""
                    if member.name == "database/opensigweave.dump":
                        data = b"changed"
                    info = tarfile.TarInfo(member.name)
                    info.size = len(data)
                    changed.addfile(info, __import__("io").BytesIO(data))
            with self.assertRaises(BackupArchiveError):
                verify_archive(tampered)

    def test_path_traversal_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            archive = base / "evil.tar.gz"
            manifest = {
                "format": "opensigweave-backup",
                "format_version": 1,
                "created_at": "2026-11-12T00:00:00+00:00",
                "metadata": {},
                "files": [],
            }
            with tarfile.open(archive, "w:gz") as output:
                payload = json.dumps(manifest).encode()
                info = tarfile.TarInfo("manifest.json")
                info.size = len(payload)
                output.addfile(info, __import__("io").BytesIO(payload))
                evil = b"owned"
                info = tarfile.TarInfo("../outside.txt")
                info.size = len(evil)
                output.addfile(info, __import__("io").BytesIO(evil))
            with self.assertRaises(BackupArchiveError):
                verify_archive(archive)
            self.assertFalse((base.parent / "outside.txt").exists())

    def test_extract_requires_a_new_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "stage"
            source.mkdir()
            archive = base / "backup.tar.gz"
            self._make_archive(source, archive)
            existing = base / "existing"
            existing.mkdir()
            with self.assertRaises(BackupArchiveError):
                extract_archive(archive, existing)

    def test_manifest_digest_is_sha256(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload.bin"
            payload.write_bytes(b"payload")
            manifest = write_manifest(root)
            self.assertEqual(manifest["files"][0]["sha256"], hashlib.sha256(b"payload").hexdigest())


if __name__ == "__main__":
    unittest.main()
