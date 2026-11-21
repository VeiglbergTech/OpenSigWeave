"""Safe restore of a complete OpenSigWeave backup into a fresh Compose project.

This module intentionally refuses existing target directories and never removes
Docker volumes. A failed restore leaves its new deployment and volumes in place
for inspection instead of attempting risky automatic cleanup.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import IO

from backup_archive import BackupArchiveError, extract_archive
from backup_encryption import (
    BackupEncryptionError,
    decrypt_and_verify_archive,
    decrypt_archive,
    decrypt_file,
)


class BackupRestoreError(Exception):
    """Raised when a safe fresh-deployment restore cannot be completed."""


def _run(
    command: list[str],
    *,
    cwd: Path,
    stdin: IO[bytes] | None = None,
    timeout: int = 900,
) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            stdin=stdin,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise BackupRestoreError(f"Required executable is unavailable: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise BackupRestoreError(f"Restore command timed out: {command[0]}") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        if detail:
            detail = detail[-1200:]
            raise BackupRestoreError(
                f"Restore command failed: {command[0]} (exit {result.returncode}): {detail}"
            )
        raise BackupRestoreError(f"Restore command failed: {command[0]} (exit {result.returncode})")
    return result


def _compose(docker: str, project_name: str, directory: Path, *args: str, timeout: int = 900):
    return _run(
        [
            docker, "compose", "--project-name", project_name,
            "--file", str(directory / "compose.yaml"),
            "--file", str(directory / "compose.restore.yaml"),
            *args,
        ],
        cwd=directory,
        timeout=timeout,
    )


def _wait_healthy(docker: str, project_name: str, directory: Path, service: str, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    last_state = "not-created"
    while time.monotonic() < deadline:
        result = _compose(docker, project_name, directory, "ps", "-q", service, timeout=30)
        container_id = result.stdout.strip()
        if container_id:
            state = _run(
                [docker, "inspect", "--format", "{{.State.Health.Status}}", container_id],
                cwd=directory,
                timeout=30,
            ).stdout.strip()
            last_state = state
            if state == "healthy":
                return
            if state == "unhealthy":
                raise BackupRestoreError(f"Restored service {service} became unhealthy")
        time.sleep(2)
    raise BackupRestoreError(f"Timed out waiting for {service}; last health state: {last_state}")


def _restore_volume(
    docker: str,
    project_name: str,
    directory: Path,
    image: str,
    volume: str,
    source: Path,
    helper_name: str,
) -> None:
    _run(
        [docker, "volume", "create", volume],
        cwd=directory,
    )
    _run(
        [
            docker, "create", "--name", helper_name, "--user", "0:0",
            "--volume", f"{volume}:/restore",
            "--entrypoint", "sh", image, "-c", "sleep 600",
        ],
        cwd=directory,
    )
    try:
        _run([docker, "start", helper_name], cwd=directory)
        _run(
            [docker, "cp", f"{source}/.", f"{helper_name}:/restore"],
            cwd=directory,
            timeout=900,
        )
        _run(
            [
                docker, "exec", helper_name, "sh", "-c",
                "find /restore -type d -exec chmod 0700 {} + "
                "&& find /restore -type f -exec chmod 0600 {} + "
                "&& chown -R 10001:10001 /restore",
            ],
            cwd=directory,
            timeout=900,
        )
    finally:
        # The helper is disposable; volume contents are deliberately retained.
        subprocess.run(
            [docker, "rm", "--force", helper_name],
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )



def _file_inventory(root: Path) -> dict[str, str]:
    inventory: dict[str, str] = {}
    if root.is_symlink() or not root.is_dir():
        raise BackupRestoreError("Restored volume snapshot is not a regular directory")
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BackupRestoreError("Restored volume contains a symbolic link")
        if path.is_dir():
            continue
        if not path.is_file():
            raise BackupRestoreError("Restored volume contains a non-regular file")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        inventory[path.relative_to(root).as_posix()] = digest.hexdigest()
    return inventory


def _verify_restored_volume(
    docker: str,
    project_name: str,
    directory: Path,
    image: str,
    volume: str,
    manifest_files: list[dict],
    manifest_prefix: str,
) -> None:
    expected = {
        entry["path"][len(manifest_prefix):]: entry["sha256"]
        for entry in manifest_files
        if entry["path"].startswith(manifest_prefix)
    }
    helper_name = f"{project_name[:36]}-verify-{manifest_prefix.split('/')[-2]}"
    _run(
        [
            docker, "create", "--name", helper_name, "--user", "0:0",
            "--volume", f"{volume}:/restore:ro",
            "--entrypoint", "sh", image, "-c", "sleep 600",
        ],
        cwd=directory,
    )
    try:
        _run([docker, "start", helper_name], cwd=directory)
        with tempfile.TemporaryDirectory(prefix="opensigweave-volume-check-") as temporary:
            snapshot = Path(temporary)
            os.chmod(snapshot, 0o700)
            _run(
                [docker, "cp", f"{helper_name}:/restore/.", str(snapshot)],
                cwd=directory,
                timeout=900,
            )
            actual = _file_inventory(snapshot)
        if actual != expected:
            raise BackupRestoreError(
                f"Restored volume integrity check failed for {manifest_prefix.rstrip('/')}"
            )
    finally:
        subprocess.run(
            [docker, "rm", "--force", helper_name],
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

def restore_fresh_deployment(
    encrypted_backup: str | Path,
    identity_file: str | Path,
    target_directory: str | Path,
    *,
    project_name: str,
    docker_binary: str = "docker",
    age_binary: str = "age",
    health_timeout: int = 180,
) -> Path:
    """Restore a full backup to a brand-new directory and new named volumes."""
    source = Path(encrypted_backup).expanduser()
    identity = Path(identity_file).expanduser()
    target = Path(target_directory).expanduser().absolute()

    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", project_name):
        raise BackupRestoreError("Compose project name must be a safe lowercase slug")
    if health_timeout < 1:
        raise BackupRestoreError("Health timeout must be at least one second")
    if source.is_symlink() or not source.is_file():
        raise BackupRestoreError("Encrypted backup is missing or unsafe")
    if identity.is_symlink() or not identity.is_file():
        raise BackupRestoreError("Age identity file is missing or unsafe")
    if os.name == "posix" and identity.stat().st_mode & 0o077:
        raise BackupRestoreError("Age identity file must not be accessible by group or other users")
    if target.exists() or target.is_symlink():
        raise BackupRestoreError("Restore target must not already exist")
    target.parent.mkdir(parents=True, exist_ok=True)

    # Verify the complete encrypted artifact before creating target files or volumes.
    try:
        manifest = decrypt_and_verify_archive(source, identity, executable=age_binary)
    except (BackupEncryptionError, BackupArchiveError) as exc:
        raise BackupRestoreError("Backup could not be decrypted and verified") from exc

    if manifest.get("metadata", {}).get("backup_kind") != "full":
        raise BackupRestoreError("Only complete disaster-recovery backups can be restored")
    expected_revision = manifest.get("metadata", {}).get("alembic_revision")
    if not isinstance(expected_revision, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", expected_revision):
        raise BackupRestoreError("Backup does not contain a valid Alembic migration revision")
    required = {
        "database/opensigweave.dump",
        "deployment/compose.yaml",
        "deployment/env.enc",
        "images/images.tar",
    }
    inventory = {entry["path"] for entry in manifest.get("files", [])}
    if not required.issubset(inventory):
        raise BackupRestoreError("Backup is missing mandatory full-restore components")
    metadata = manifest["metadata"]
    app_image_id = metadata.get("application_image_id")
    if (
        not isinstance(app_image_id, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", app_image_id)
        or not isinstance(metadata.get("application_image_ref"), str)
        or not metadata["application_image_ref"].strip()
        or not isinstance(metadata.get("postgres_image_ref"), str)
        or not metadata["postgres_image_ref"].strip()
    ):
        raise BackupRestoreError("Backup does not contain valid application and PostgreSQL image identities")

    # Refuse to collide with any deployment before creating the target directory.
    existing_project_containers = _run(
        [
            docker_binary, "ps", "-aq",
            "--filter", f"label=com.docker.compose.project={project_name}",
        ],
        cwd=target.parent,
    ).stdout.strip()
    if existing_project_containers:
        raise BackupRestoreError("A Docker Compose project with this name already exists")
    expected_volumes = {
        f"{project_name}_opensigweave-postgres-data",
        f"{project_name}_opensigweave-signatures",
        f"{project_name}_opensigweave-idp-data",
    }
    existing_volumes = set(
        _run(
            [docker_binary, "volume", "ls", "--format", "{{.Name}}"],
            cwd=target.parent,
        ).stdout.splitlines()
    )
    collisions = expected_volumes & existing_volumes
    if collisions:
        raise BackupRestoreError(
            "Restore refused because target volume names already exist: "
            + ", ".join(sorted(collisions))
        )

    try:
        with tempfile.TemporaryDirectory(prefix="opensigweave-restore-") as temporary:
            work = Path(temporary)
            os.chmod(work, 0o700)
            archive = work / "backup.tar.gz"
            decrypt_archive(source, identity, archive, executable=age_binary)
            extracted = work / "extracted"
            extract_archive(archive, extracted)
            (extracted / "data" / "signatures").mkdir(parents=True, exist_ok=True)
            (extracted / "data" / "idp-config").mkdir(parents=True, exist_ok=True)

            target.mkdir(mode=0o700)
            os.chmod(target, 0o700)
            shutil.copyfile(extracted / "deployment" / "compose.yaml", target / "compose.yaml")
            os.chmod(target / "compose.yaml", 0o600)
            decrypt_file(
                extracted / "deployment" / "env.enc",
                identity,
                target / ".env",
                executable=age_binary,
            )

            # The restored deployment uses the exact image saved in the backup.
            override = (
                "services:\n"
                "  opensigweave:\n"
                "    image: opensigweave:restore\n"
                "    build: null\n"
            )
            (target / "compose.restore.yaml").write_text(override, encoding="utf-8")
            os.chmod(target / "compose.restore.yaml", 0o600)

            _run([docker_binary, "image", "load", "--input", str(extracted / "images" / "images.tar")],
                 cwd=target, timeout=1800)
            _run([docker_binary, "image", "tag", app_image_id, "opensigweave:restore"],
                 cwd=target, timeout=120)

            _compose(docker_binary, project_name, target, "config", "--quiet")
            services = _compose(docker_binary, project_name, target, "config", "--services").stdout.splitlines()
            if not {"postgres", "opensigweave"}.issubset(set(services)):
                raise BackupRestoreError("Restored Compose definition is missing required services")

            # Refuse any existing volume name: "docker volume create" would
            # otherwise return an existing volume and could overwrite user data.
            volume_names = {
                f"{project_name}_opensigweave-postgres-data",
                f"{project_name}_opensigweave-signatures",
                f"{project_name}_opensigweave-idp-data",
            }
            existing_volumes = set(
                _run(
                    [docker_binary, "volume", "ls", "--format", "{{.Name}}"],
                    cwd=target,
                ).stdout.splitlines()
            )
            collisions = volume_names & existing_volumes
            if collisions:
                raise BackupRestoreError(
                    "Restore refused because target volume names already exist: "
                    + ", ".join(sorted(collisions))
                )
            for volume in sorted(volume_names):
                _run([docker_binary, "volume", "create", volume], cwd=target)

            _restore_volume(
                docker_binary, project_name, target, "opensigweave:restore",
                f"{project_name}_opensigweave-signatures",
                extracted / "data" / "signatures",
                f"{project_name}-restore-signatures",
            )
            _restore_volume(
                docker_binary, project_name, target, "opensigweave:restore",
                f"{project_name}_opensigweave-idp-data",
                extracted / "data" / "idp-config",
                f"{project_name}-restore-idp",
            )

            _compose(docker_binary, project_name, target, "up", "-d", "postgres", timeout=300)
            _wait_healthy(docker_binary, project_name, target, "postgres", health_timeout)

            dump = (extracted / "database" / "opensigweave.dump").open("rb")
            try:
                _run(
                    [
                        docker_binary, "compose", "--project-name", project_name,
                        "--file", str(target / "compose.yaml"),
                        "--file", str(target / "compose.restore.yaml"),
                        "exec", "-T", "postgres", "sh", "-c",
                        'exec pg_restore --exit-on-error -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
                    ],
                    cwd=target,
                    stdin=dump,
                    timeout=1800,
                )
            finally:
                dump.close()

            restored_revision = _run(
                [
                    docker_binary, "compose", "--project-name", project_name,
                    "--file", str(target / "compose.yaml"),
                    "--file", str(target / "compose.restore.yaml"),
                    "exec", "-T", "postgres", "sh", "-c",
                    'exec psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT version_num FROM alembic_version"',
                ],
                cwd=target,
                timeout=60,
            ).stdout.strip()
            if restored_revision != expected_revision:
                raise BackupRestoreError(
                    "Restored database migration revision does not match the backup manifest"
                )

            _verify_restored_volume(
                docker_binary, project_name, target, "opensigweave:restore",
                f"{project_name}_opensigweave-signatures",
                manifest["files"], "data/signatures/",
            )
            _verify_restored_volume(
                docker_binary, project_name, target, "opensigweave:restore",
                f"{project_name}_opensigweave-idp-data",
                manifest["files"], "data/idp-config/",
            )

            _compose(docker_binary, project_name, target, "up", "-d", "opensigweave", timeout=600)
            _wait_healthy(docker_binary, project_name, target, "opensigweave", health_timeout)

            return target
    except (BackupEncryptionError, BackupArchiveError, OSError, subprocess.SubprocessError) as exc:
        raise BackupRestoreError(f"Restore failed; any newly created target data was retained: {exc}") from exc
