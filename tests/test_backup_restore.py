import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backup_archive import create_archive, write_manifest
from backup_restore import BackupRestoreError, restore_fresh_deployment


class BackupRestoreTests(unittest.TestCase):
    def _backup(self, root: Path) -> tuple[Path, dict]:
        staging = root / "staging"
        (staging / "database").mkdir(parents=True)
        (staging / "deployment").mkdir()
        (staging / "images").mkdir()
        (staging / "data" / "signatures").mkdir(parents=True)
        (staging / "data" / "idp-config").mkdir(parents=True)
        (staging / "database" / "opensigweave.dump").write_bytes(b"postgres dump")
        (staging / "deployment" / "compose.yaml").write_text(
            "services:\n  postgres:\n    image: postgres:17-alpine\n  opensigweave:\n    build: .\n",
            encoding="utf-8",
        )
        (staging / "deployment" / "env.enc").write_bytes(b"encrypted env")
        (staging / "images" / "images.tar").write_bytes(b"docker images")
        (staging / "data" / "signatures" / "signature.html").write_text("<p>hello</p>", encoding="utf-8")
        (staging / "data" / "idp-config" / "provider.env").write_text("TOKEN=secret", encoding="utf-8")
        manifest = write_manifest(staging, {
            "backup_kind": "full",
            "application_image_id": "sha256:" + "a" * 64,
            "postgres_image_id": "sha256:" + "b" * 64,
            "application_image_ref": "opensigweave:preview",
            "postgres_image_ref": "postgres:17-alpine",
            "alembic_revision": "revision_001",
        })
        archive = root / "backup.tar.gz"
        create_archive(staging, archive)
        encrypted = root / "backup.age"
        encrypted.write_bytes(b"encrypted")
        return encrypted, manifest

    def test_restore_builds_new_deployment_and_never_reuses_existing_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup, manifest = self._backup(root)
            identity = root / "identity"
            identity.write_text("AGE-SECRET-KEY-1TEST", encoding="utf-8")
            identity.chmod(0o600)
            target = root / "restored"
            compose_calls = []

            def compose(docker, project, directory, *args, timeout=900):
                compose_calls.append(args)
                output = "postgres\nopensigweave\n" if args == ("config", "--services") else ""
                return subprocess.CompletedProcess([], 0, output, "")

            def run(command, *, cwd, stdin=None, timeout=900):
                output = "healthy" if command[1:3] == ["inspect", "--format"] else ""
                if "SELECT version_num FROM alembic_version" in " ".join(command):
                    output = "revision_001"
                return subprocess.CompletedProcess(command, 0, output, "")

            def decrypt_archive(source, identity_file, destination, **kwargs):
                shutil.copyfile(root / "backup.tar.gz", destination)
                return Path(destination)

            with (
                patch("backup_restore.decrypt_and_verify_archive", return_value=manifest),
                patch("backup_restore.decrypt_archive", side_effect=decrypt_archive),
                patch("backup_restore.decrypt_file", side_effect=lambda src, ident, dest, **kw: Path(dest).write_text("SECRET=value")),
                patch("backup_restore._compose", side_effect=compose),
                patch("backup_restore._run", side_effect=run),
                patch("backup_restore._restore_volume") as restore_volume,
                patch("backup_restore._verify_restored_volume") as verify_volume,
                patch("backup_restore._wait_healthy"),
            ):
                result = restore_fresh_deployment(
                    backup, identity, target, project_name="opensigweave-dr"
                )

            self.assertEqual(result, target)
            self.assertEqual((target / ".env").read_text(), "SECRET=value")
            self.assertTrue((target / "compose.yaml").is_file())
            self.assertIn(("up", "-d", "postgres"), compose_calls)
            self.assertIn(("up", "-d", "opensigweave"), compose_calls)
            self.assertEqual(restore_volume.call_count, 2)
            self.assertEqual(verify_volume.call_count, 2)

    def test_existing_target_is_rejected_before_any_docker_action(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup, _ = self._backup(root)
            identity = root / "identity"
            identity.write_text("identity", encoding="utf-8")
            identity.chmod(0o600)
            target = root / "existing"
            target.mkdir()
            with patch("backup_restore._run") as run:
                with self.assertRaises(BackupRestoreError):
                    restore_fresh_deployment(
                        backup, identity, target, project_name="opensigweave-dr"
                    )
                run.assert_not_called()

    def test_existing_project_volume_is_never_reused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup, manifest = self._backup(root)
            identity = root / "identity"
            identity.write_text("identity", encoding="utf-8")
            identity.chmod(0o600)
            target = root / "restore-target"

            def run(command, *, cwd, stdin=None, timeout=900):
                output = ""
                if command[1:3] == ["volume", "ls"]:
                    output = "opensigweave-dr_opensigweave-signatures\\n"
                return subprocess.CompletedProcess(command, 0, output, "")

            with (
                patch("backup_restore.decrypt_and_verify_archive", return_value=manifest),
                patch("backup_restore.decrypt_archive", side_effect=lambda src, ident, dest, **kw: shutil.copyfile(root / "backup.tar.gz", dest)),
                patch("backup_restore.decrypt_file", side_effect=lambda src, ident, dest, **kw: Path(dest).write_text("SECRET=value")),
                patch("backup_restore._compose", side_effect=lambda docker, project, directory, *args, **kw: subprocess.CompletedProcess([], 0, "postgres\\nopensigweave\\n" if args == ("config", "--services") else "", "")),
                patch("backup_restore._run", side_effect=run),
            ):
                with self.assertRaises(BackupRestoreError):
                    restore_fresh_deployment(
                        backup, identity, target, project_name="opensigweave-dr"
                    )

    def test_partial_backup_is_not_restorable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup, manifest = self._backup(root)
            identity = root / "identity"
            identity.write_text("identity", encoding="utf-8")
            identity.chmod(0o600)
            manifest["metadata"]["backup_kind"] = "prepared-staging"
            with patch("backup_restore.decrypt_and_verify_archive", return_value=manifest):
                with self.assertRaises(BackupRestoreError):
                    restore_fresh_deployment(
                        backup, identity, root / "target", project_name="opensigweave-dr"
                    )


if __name__ == "__main__":
    unittest.main()
