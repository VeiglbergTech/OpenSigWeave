import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backup_docker
from backup_docker import BackupDockerError, create_docker_backup


class DockerBackupTests(unittest.TestCase):
    def _project(self, root: Path) -> tuple[Path, Path]:
        project = root / "project"
        project.mkdir()
        (project / "compose.yaml").write_text(
            "services:\n  postgres: {}\n  opensigweave: {}\n", encoding="utf-8"
        )
        (project / ".env").write_text("POSTGRES_PASSWORD=secret\nSECRET_KEY=secret2\n", encoding="utf-8")
        identity = root / "age.identity"
        identity.write_text("AGE-SECRET-KEY-1TEST", encoding="utf-8")
        identity.chmod(0o600)
        return project, identity

    def test_full_backup_snapshots_components_and_restarts_app(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project, identity = self._project(root)
            output = root / "backups" / "full.age"
            calls = []

            def run(command, *, cwd, stdout=None, timeout=900):
                calls.append(command)
                result_stdout = ""
                if command[1:4] == ["compose", "config", "--services"]:
                    result_stdout = "postgres\nopensigweave\n"
                elif command[1:4] == ["compose", "ps", "-q"]:
                    result_stdout = "app-container\n" if command[-1] == "opensigweave" else "db-container\n"
                elif command[1:3] == ["inspect", "--format"]:
                    if command[2] == "{{.State.Running}}":
                        result_stdout = "true"
                    elif command[2] == "{{json .Mounts}}":
                        result_stdout = json.dumps([
                            {"Type": "volume", "Destination": "/data/signatures"},
                            {"Type": "volume", "Destination": "/app/.idp"},
                        ])
                    else:
                        result_stdout = "sha256:app-image"
                elif command[1:3] == ["compose", "exec"]:
                    stdout.write(b"postgres custom dump")
                elif command[1:4] == ["image", "save", "--output"]:
                    Path(command[4]).write_bytes(b"saved docker images")
                elif command[1:3] == ["cp", "app-container:/data/signatures/."]:
                    Path(command[-1], "signature.html").write_text("<p>signature</p>", encoding="utf-8")
                elif command[1:3] == ["cp", "app-container:/app/.idp/."]:
                    Path(command[-1], "provider.env").write_text("TOKEN=secret", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout=result_stdout, stderr="")

            def encrypt_secret(source, destination, recipient, **kwargs):
                Path(destination).write_bytes(b"encrypted-env")
                return Path(destination)

            def encrypt_backup(source, destination, recipient, **kwargs):
                Path(destination).write_bytes(b"encrypted-backup")
                return Path(destination)

            with (
                patch("backup_docker._run", side_effect=run),
                patch("backup_docker.encrypt_file", side_effect=encrypt_secret),
                patch("backup_docker.encrypt_archive", side_effect=encrypt_backup),
                patch("backup_docker.decrypt_and_verify_archive", return_value={"files": []}),
            ):
                result = create_docker_backup(project, output, "age1recipient", identity)

            self.assertEqual(result, output)
            self.assertTrue(output.exists())
            self.assertIn(["docker", "compose", "start", "opensigweave"], calls)
            self.assertTrue(any("pg_dump" in " ".join(call) for call in calls))
            self.assertFalse(any("POSTGRES_PASSWORD=secret" in str(call) for call in calls))

    def test_app_restart_is_attempted_when_database_dump_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project, identity = self._project(root)
            calls = []

            def run(command, *, cwd, stdout=None, timeout=900):
                calls.append(command)
                if command[1:4] == ["compose", "config", "--services"]:
                    return subprocess.CompletedProcess(command, 0, "postgres\nopensigweave\n", "")
                if command[1:4] == ["compose", "ps", "-q"]:
                    return subprocess.CompletedProcess(
                        command, 0,
                        "app-container\n" if command[-1] == "opensigweave" else "db-container\n", ""
                    )
                if command[1:3] == ["inspect", "--format"]:
                    if command[2] == "{{.State.Running}}":
                        return subprocess.CompletedProcess(command, 0, "true", "")
                    return subprocess.CompletedProcess(command, 0, json.dumps([
                        {"Type": "volume", "Destination": "/data/signatures"},
                        {"Type": "volume", "Destination": "/app/.idp"},
                    ]), "")
                if command[1:3] == ["compose", "exec"]:
                    raise BackupDockerError("simulated pg_dump failure")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("backup_docker._run", side_effect=run):
                with self.assertRaises(BackupDockerError):
                    create_docker_backup(project, root / "backup.age", "age1recipient", identity)

            self.assertIn(["docker", "compose", "start", "opensigweave"], calls)

    def test_existing_backup_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project, identity = self._project(root)
            output = root / "backup.age"
            output.write_text("keep", encoding="utf-8")
            with self.assertRaises(BackupDockerError):
                create_docker_backup(project, output, "age1recipient", identity)
            self.assertEqual(output.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
