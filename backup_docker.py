"""Docker Compose snapshot orchestration for complete OpenSigWeave backups.

The command is deliberately host-side: Docker and age are never added to the
application image, and the application container does not receive the age key.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from typing import IO

from backup_archive import BackupArchiveError, create_archive, write_manifest
from backup_encryption import (
    BackupEncryptionError,
    decrypt_and_verify_archive,
    encrypt_archive,
    encrypt_file,
)


class BackupDockerError(Exception):
    """Raised when a Docker-aware backup cannot be completed safely."""


def _run(
    command: list[str],
    *,
    cwd: Path,
    stdout: IO[bytes] | None = None,
    timeout: int = 900,
) -> subprocess.CompletedProcess:
    options: dict = {"check": False, "timeout": timeout}
    if stdout is None:
        options.update(capture_output=True, text=True)
    else:
        options.update(stdout=stdout, stderr=subprocess.PIPE)
    try:
        result = subprocess.run(command, cwd=cwd, **options)
    except FileNotFoundError as exc:
        raise BackupDockerError(f"Required executable is unavailable: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise BackupDockerError("A Docker backup operation exceeded its time limit") from exc
    if result.returncode != 0:
        raise BackupDockerError(f"Command failed: {command[0]} (exit {result.returncode})")
    return result


def _secure_staging_tree(root: Path) -> None:
    """Restrict plaintext backup staging permissions and reject copied symlinks."""
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in directories:
            path = current_path / name
            if path.is_symlink():
                raise BackupDockerError("Backup volume contains a symbolic link")
            os.chmod(path, 0o700)
        for name in files:
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                raise BackupDockerError("Backup volume contains an unsafe file")
            os.chmod(path, 0o600)
        os.chmod(current_path, 0o700)


def _inspect_mounts(docker: str, container_id: str, project: Path) -> dict[str, dict]:
    result = _run(
        [docker, "inspect", "--format", "{{json .Mounts}}", container_id],
        cwd=project,
    )
    try:
        mounts = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise BackupDockerError("Docker returned invalid mount information") from exc
    if not isinstance(mounts, list):
        raise BackupDockerError("Docker mount information is incomplete")
    return {
        item.get("Destination"): item
        for item in mounts
        if isinstance(item, dict) and isinstance(item.get("Destination"), str)
    }


def create_docker_backup(
    project_directory: str | Path,
    output_path: str | Path,
    recipient: str,
    identity_file: str | Path,
    *,
    docker_binary: str = "docker",
    age_binary: str = "age",
) -> Path:
    """Create, encrypt and decrypt-verify a complete Compose backup.

    The age identity should preferably be supplied from removable/off-host
    storage for the duration of the command.
    """
    project = Path(project_directory).expanduser().resolve()
    output = Path(output_path).expanduser().absolute()
    identity = Path(identity_file).expanduser()
    compose_file = project / "compose.yaml"
    env_file = project / ".env"

    if not project.is_dir() or project.is_symlink():
        raise BackupDockerError("Compose project directory is missing or unsafe")
    if compose_file.is_symlink() or not compose_file.is_file():
        raise BackupDockerError("compose.yaml is missing or unsafe")
    if env_file.is_symlink() or not env_file.is_file():
        raise BackupDockerError(".env is missing or unsafe")
    if identity.is_symlink() or not identity.is_file():
        raise BackupDockerError("The age identity file is missing or unsafe")
    if os.name == "posix" and stat.S_IMODE(identity.stat().st_mode) & 0o077:
        raise BackupDockerError("The age identity file must not be accessible by group or other users")
    if not recipient or not recipient.strip():
        raise BackupDockerError("An age recipient is required")
    if output.exists() or output.is_symlink():
        raise BackupDockerError("Backup destination already exists")
    if output == project or project in output.parents:
        raise BackupDockerError("Backup destination must be outside the Compose project")
    output.parent.mkdir(parents=True, exist_ok=True)

    _run([docker_binary, "compose", "config", "--quiet"], cwd=project)
    services = _run([docker_binary, "compose", "config", "--services"], cwd=project)
    service_names = set(services.stdout.splitlines())
    if not {"postgres", "opensigweave"}.issubset(service_names):
        raise BackupDockerError("Compose project must contain postgres and opensigweave services")

    app_result = _run([docker_binary, "compose", "ps", "-q", "opensigweave"], cwd=project)
    db_result = _run([docker_binary, "compose", "ps", "-q", "postgres"], cwd=project)
    app_id = app_result.stdout.strip()
    db_id = db_result.stdout.strip()
    if not app_id or not db_id:
        raise BackupDockerError("Both application and PostgreSQL containers must exist and be running")
    for container_id in (app_id, db_id):
        state = _run(
            [docker_binary, "inspect", "--format", "{{.State.Running}}", container_id],
            cwd=project,
        ).stdout.strip()
        if state.lower() != "true":
            raise BackupDockerError("Both application and PostgreSQL containers must be running")

    mounts = _inspect_mounts(docker_binary, app_id, project)
    for destination in ("/data/signatures", "/app/.idp"):
        mount = mounts.get(destination)
        if not mount or mount.get("Type") != "volume":
            raise BackupDockerError(f"Required persistent volume is missing: {destination}")

    with tempfile.TemporaryDirectory(
        prefix=".opensigweave-backup-", dir=output.parent
    ) as temporary:
        work = Path(temporary)
        os.chmod(work, 0o700)
        staging = work / "staging"
        staging.mkdir(mode=0o700)
        (staging / "database").mkdir()
        (staging / "data" / "signatures").mkdir(parents=True)
        (staging / "data" / "idp-config").mkdir(parents=True)
        (staging / "deployment").mkdir()
        (staging / "images").mkdir()

        stopped = False
        published = False
        try:
            # Set this before stop: a failed stop may still have partially stopped
            # the service, so the finally block must always attempt recovery.
            stopped = True
            _run(
                [docker_binary, "compose", "stop", "--timeout", "30", "opensigweave"],
                cwd=project,
                timeout=60,
            )

            dump_path = staging / "database" / "opensigweave.dump"
            with dump_path.open("wb") as dump:
                _run(
                    [
                        docker_binary, "compose", "exec", "-T", "postgres", "sh", "-c",
                        'exec pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
                    ],
                    cwd=project,
                    stdout=dump,
                    timeout=1800,
                )
            if dump_path.stat().st_size == 0:
                raise BackupDockerError("PostgreSQL produced an empty dump")

            migration_revision = _run(
                [
                    docker_binary, "compose", "exec", "-T", "postgres", "sh", "-c",
                    'exec psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT version_num FROM alembic_version"',
                ],
                cwd=project,
                timeout=60,
            ).stdout.strip()
            if not migration_revision or "\\n" in migration_revision:
                raise BackupDockerError("Unable to determine a single Alembic migration revision")

            _run(
                [docker_binary, "cp", f"{app_id}:/data/signatures/.", str(staging / "data" / "signatures")],
                cwd=project,
                timeout=900,
            )
            _run(
                [docker_binary, "cp", f"{app_id}:/app/.idp/.", str(staging / "data" / "idp-config")],
                cwd=project,
                timeout=900,
            )

            shutil.copyfile(compose_file, staging / "deployment" / "compose.yaml")
            os.chmod(staging / "deployment" / "compose.yaml", 0o600)
            encrypt_file(
                env_file,
                staging / "deployment" / "env.enc",
                recipient,
                executable=age_binary,
            )

            app_image_id = _run(
                [docker_binary, "inspect", "--format", "{{.Image}}", app_id],
                cwd=project,
            ).stdout.strip()
            db_image_id = _run(
                [docker_binary, "inspect", "--format", "{{.Image}}", db_id],
                cwd=project,
            ).stdout.strip()
            app_image_ref = _run(
                [docker_binary, "inspect", "--format", "{{.Config.Image}}", app_id],
                cwd=project,
            ).stdout.strip()
            db_image_ref = _run(
                [docker_binary, "inspect", "--format", "{{.Config.Image}}", db_id],
                cwd=project,
            ).stdout.strip()
            if not all((app_image_id, db_image_id, app_image_ref, db_image_ref)):
                raise BackupDockerError("Unable to identify application and PostgreSQL images")
            _run(
                [
                    docker_binary, "image", "save", "--output",
                    str(staging / "images" / "images.tar"), app_image_ref, db_image_ref,
                ],
                cwd=project,
                timeout=1800,
            )
            _secure_staging_tree(staging)
            manifest = write_manifest(
                staging,
                {
                    "backup_kind": "full",
                    "application_image_id": app_image_id,
                    "postgres_image_id": db_image_id,
                    "application_image_ref": app_image_ref,
                    "postgres_image_ref": db_image_ref,
                    "database_dump_format": "pg_dump-custom",
                    "alembic_revision": migration_revision,
                    "included_components": [
                        "postgresql", "signatures", "identity-provider-config",
                        "compose-definition", "encrypted-environment",
                    ],
                },
            )
            if not {"database/opensigweave.dump", "deployment/env.enc", "deployment/compose.yaml", "images/images.tar"}.issubset(
                {entry["path"] for entry in manifest["files"]}
            ):
                raise BackupDockerError("A mandatory backup component is missing")

            archive = work / "opensigweave-backup.tar.gz"
            create_archive(staging, archive)
            encrypt_archive(archive, output, recipient, executable=age_binary)
            published = True
            decrypt_and_verify_archive(output, identity, executable=age_binary)
        except (BackupArchiveError, BackupEncryptionError, OSError) as exc:
            if published:
                output.unlink(missing_ok=True)
            raise BackupDockerError(f"Backup creation failed: {exc}") from exc
        finally:
            if stopped:
                try:
                    _run(
                        [docker_binary, "compose", "start", "opensigweave"],
                        cwd=project,
                        timeout=120,
                    )
                except BackupDockerError:
                    if published:
                        output.unlink(missing_ok=True)
                    raise BackupDockerError(
                        "Backup operation ended, but the application service could not be restarted"
                    )
        return output
