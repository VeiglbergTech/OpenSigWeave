"""In-process, Docker-socket-free encrypted application snapshots.

One Uvicorn process is required: its request gate quiesces application writes
while PostgreSQL and the two persistent file stores are copied. External writers
must be stopped separately. Legacy host-created full archives remain readable by
the separate recovery CLI; application backups are a different restore profile.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import threading
from typing import Callable, Iterator

from sqlalchemy.engine import make_url

from backup_archive import BackupArchiveError, create_archive, extract_archive, write_manifest
from backup_encryption import BackupEncryptionError, decrypt_archive, encrypt_archive
from backup_locations import local_root


class BackupEngineError(Exception):
    """Safe, intentionally non-secret failure in the container backup engine."""


# Acquired for every web request and held during the database/file snapshot.
# Multi-process Uvicorn and external writers cannot share this Python lock.
backup_gate = threading.Lock()


def _run_pg(args: list[str], *, password: str, timeout: int = 1800) -> str:
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    env["PGCONNECT_TIMEOUT"] = "10"
    try:
        result = subprocess.run(
            args, env=env, check=False, capture_output=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        raise BackupEngineError("PostgreSQL snapshot tool is unavailable or timed out") from None
    if result.returncode:
        # Never surface stderr: libpq errors can contain connection/secret data.
        raise BackupEngineError("PostgreSQL snapshot command failed")
    return result.stdout.decode("utf-8", errors="replace").strip()


def _pg_commands() -> tuple[list[str], str]:
    url = make_url(os.environ.get("DATABASE_URL", ""))
    if url.get_backend_name() != "postgresql" or not url.database or not url.username:
        raise BackupEngineError("Application backups require PostgreSQL")
    args = [
        "--host", url.host or "localhost", "--port", str(url.port or 5432),
        "--username", url.username, "--dbname", url.database,
    ]
    return args, url.password or ""


def _copy_private_tree(source: Path, destination: Path) -> None:
    """Reject symlinks and special files; never recurse outside managed roots."""
    if source.is_symlink() or not source.is_dir():
        raise BackupEngineError("Persistent application storage is missing or unsafe")
    destination.mkdir(mode=0o700, parents=True)
    for entry in os.scandir(source):
        src, dst = Path(entry.path), destination / entry.name
        if entry.is_symlink():
            raise BackupEngineError("Persistent storage contains an unsafe symbolic link")
        if entry.is_dir(follow_symlinks=False):
            _copy_private_tree(src, dst)
        elif entry.is_file(follow_symlinks=False):
            fd = os.open(src, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as reader:
                before = os.fstat(reader.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise BackupEngineError("Persistent storage contains an unsafe file")
                with dst.open("xb") as writer:
                    os.chmod(dst, 0o600)
                    shutil.copyfileobj(reader, writer, length=1024 * 1024)
                after = os.fstat(reader.fileno())
                if ((before.st_size, before.st_mtime_ns, before.st_ino) !=
                        (after.st_size, after.st_mtime_ns, after.st_ino)):
                    raise BackupEngineError("Persistent storage changed during snapshot")
        else:
            raise BackupEngineError("Persistent storage contains an unsupported file type")


@contextmanager
def _identity(work: Path) -> Iterator[Path]:
    value = os.environ.get("BACKUP_AGE_IDENTITY", "").strip()
    filename = os.environ.get("BACKUP_AGE_IDENTITY_FILE", "").strip()
    if bool(value) == bool(filename):
        raise BackupEngineError("Set exactly one age recovery identity source")
    if value:
        if not value.startswith("AGE-SECRET-KEY-") or "\n" in value or "\r" in value:
            raise BackupEngineError("Invalid age recovery identity")
        path = work / "age-identity.txt"
        with path.open("x", encoding="ascii") as writer:
            os.chmod(path, 0o600)
            writer.write(value + "\n")
        yield path
    else:
        path = Path(filename)
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise BackupEngineError("Age recovery identity file must be private (0600)")
        yield path


def _work_root() -> Path:
    root = Path(os.environ.get("BACKUP_WORK_ROOT", str(local_root()))).absolute()
    if root.is_symlink():
        raise BackupEngineError("Backup staging directory cannot be a symlink")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.stat()
    if (not stat.S_ISDIR(info.st_mode) or
            (hasattr(os, "geteuid") and info.st_uid != os.geteuid()) or
            stat.S_IMODE(info.st_mode) & 0o077):
        raise BackupEngineError("Backup staging directory must be private (0700)")
    return root


def create_application_backup(deliver: Callable[[Path, int, str], None]) -> tuple[int, str]:
    """Snapshot, authenticate and deliver one encrypted archive.

    The callback publishes ciphertext atomically to the selected destination.
    Failures discard work and must never mark a run successful.
    """
    recipient = os.environ.get("BACKUP_AGE_RECIPIENT", "").strip()
    if not recipient.startswith("age1") or len(recipient) > 200:
        raise BackupEngineError("Configure BACKUP_AGE_RECIPIENT before starting backups")
    if os.environ.get("UVICORN_WORKERS", "1") != "1":
        raise BackupEngineError("Application backups require UVICORN_WORKERS=1 for consistent file snapshots")
    root = _work_root()
    with tempfile.TemporaryDirectory(prefix=".osw-snapshot-", dir=root) as name:
        work = Path(name)
        staging = work / "staging"
        (staging / "database").mkdir(parents=True, mode=0o700)
        with _identity(work) as identity:
            # The gate also blocks writes to the signature/IDP volumes by HTTP
            # handlers. It must span *both* the dump and file copies.
            with backup_gate:
                pg_args, password = _pg_commands()
                revision = _run_pg(["psql", *pg_args, "-At", "-c",
                                    "SELECT version_num FROM alembic_version"], password=password, timeout=60)
                if not revision or len(revision.splitlines()) != 1:
                    raise BackupEngineError("Unable to determine a single Alembic revision")
                dump = staging / "database" / "opensigweave.dump"
                _run_pg(["pg_dump", *pg_args, "--format=custom", "--no-owner",
                         "--no-acl", "--file", str(dump)], password=password)
                if not dump.is_file() or dump.stat().st_size == 0:
                    raise BackupEngineError("PostgreSQL produced no database snapshot")
                _copy_private_tree(
                    Path(os.environ.get("SIGNATURE_STORAGE_PATH", "/data/signatures")),
                    staging / "data" / "signatures",
                )
                _copy_private_tree(
                    Path(os.environ.get("IDP_CONFIG_DIR", "/app/.idp")),
                    staging / "data" / "idp-config",
                )
            write_manifest(staging, {
                "backup_kind": "application",
                "database_dump_format": "pg_dump-custom",
                "alembic_revision": revision,
                "included_components": ["postgresql", "signatures", "identity-provider-config"],
                "restore_requires": "fresh-deployment-source-and-configuration",
            })
            plaintext = work / "backup.tar.gz"
            encrypted = work / "backup.age"
            create_archive(staging, plaintext)
            encrypt_archive(plaintext, encrypted, recipient)
            # A successful encryption alone is not sufficient: decrypt and
            # verify every manifest digest before reporting a completed run.
            verified = work / "verified.tar.gz"
            decrypt_archive(encrypted, identity, verified)
            verified.unlink()
            size = encrypted.stat().st_size
            sha = hashlib.sha256()
            with encrypted.open("rb") as reader:
                for block in iter(lambda: reader.read(1024 * 1024), b""):
                    sha.update(block)
            if size <= 0:
                raise BackupEngineError("Encrypted archive is empty")
            digest = sha.hexdigest()
            deliver(encrypted, size, digest)
            return size, digest


def restore_application_to_new_directory(
    source: str | Path, identity: str | Path, target: str | Path
) -> dict:
    """Offline, non-destructive verified recovery extraction, never live restore."""
    source, identity, target = Path(source), Path(identity), Path(target).absolute()
    if target.exists() or target.is_symlink():
        raise BackupEngineError("Refusing to restore over an existing directory")
    if source.is_symlink() or not source.is_file():
        raise BackupEngineError("Encrypted backup is missing or unsafe")
    if identity.is_symlink() or not identity.is_file() or stat.S_IMODE(identity.stat().st_mode) & 0o077:
        raise BackupEngineError("Age identity is missing or not private")
    with tempfile.TemporaryDirectory(prefix="osw-restore-", dir=_work_root()) as name:
        plain = Path(name) / "backup.tar.gz"
        decrypt_archive(source, identity, plain)
        from backup_archive import verify_archive
        metadata = verify_archive(plain)
        if metadata.get("metadata", {}).get("backup_kind") != "application":
            raise BackupEngineError("Not an application-profile backup; use the legacy fresh restore CLI")
        required = {"database/opensigweave.dump"}
        if not required.issubset({file["path"] for file in metadata["files"]}):
            raise BackupEngineError("Application backup is missing the PostgreSQL dump")
        return extract_archive(plain, target)
