"""Versioned, integrity-checked OpenSigWeave backup archive primitives.

This module handles plaintext archive structure only. Encryption, Docker volume
snapshotting, and orchestration belong to the backup command layer.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
from typing import Any


FORMAT_NAME = "opensigweave-backup"
FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
CHUNK_SIZE = 1024 * 1024
MAX_FILES = 100_000
MAX_TOTAL_BYTES = 10 * 1024 * 1024 * 1024


class BackupArchiveError(Exception):
    """Raised when a backup archive is invalid or cannot be safely handled."""


def _safe_relative_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise BackupArchiveError("Backup contains an invalid path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BackupArchiveError("Backup contains an unsafe path")
    if path.as_posix() != value or value == MANIFEST_NAME:
        raise BackupArchiveError("Backup contains a non-canonical or reserved path")
    return path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(root: Path) -> list[dict[str, Any]]:
    if root.is_symlink() or not root.is_dir():
        raise BackupArchiveError("Backup staging root must be a real directory")
    entries: list[dict[str, Any]] = []
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BackupArchiveError("Backup staging data must not contain symlinks")
        if path.is_dir():
            continue
        if not path.is_file():
            raise BackupArchiveError("Backup staging data contains a non-regular file")
        relative = path.relative_to(root).as_posix()
        if relative == MANIFEST_NAME:
            continue
        _safe_relative_path(relative)
        size = path.stat().st_size
        total += size
        if len(entries) >= MAX_FILES or total > MAX_TOTAL_BYTES:
            raise BackupArchiveError("Backup staging data exceeds configured limits")
        entries.append({"path": relative, "size_bytes": size, "sha256": _sha256_file(path)})
    return entries


def write_manifest(staging_directory: str | Path, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Inventory staged payloads and atomically write manifest.json."""
    root = Path(staging_directory)
    files = _inventory(root)
    manifest = {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata or {},
        "files": files,
    }
    if not isinstance(manifest["metadata"], dict):
        raise BackupArchiveError("Backup metadata must be an object")
    destination = root / MANIFEST_NAME
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=root, prefix=".manifest-", suffix=".tmp", delete=False
        ) as temporary:
            temporary_name = temporary.name
            os.chmod(temporary_name, 0o600)
            json.dump(manifest, temporary, sort_keys=True, separators=(",", ":"))
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, destination)
        os.chmod(destination, 0o600)
    except OSError as exc:
        raise BackupArchiveError("Unable to write backup manifest") from exc
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return manifest


def _validate_manifest(raw: bytes) -> dict[str, Any]:
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupArchiveError("Backup manifest is not valid UTF-8 JSON") from exc
    if not isinstance(manifest, dict):
        raise BackupArchiveError("Backup manifest must be an object")
    if manifest.get("format") != FORMAT_NAME or manifest.get("format_version") != FORMAT_VERSION:
        raise BackupArchiveError("Unsupported backup format or version")
    if not isinstance(manifest.get("metadata"), dict) or not isinstance(manifest.get("files"), list):
        raise BackupArchiveError("Backup manifest has an invalid structure")
    if len(manifest["files"]) > MAX_FILES:
        raise BackupArchiveError("Backup manifest contains too many files")
    seen: set[str] = set()
    total = 0
    for item in manifest["files"]:
        if not isinstance(item, dict):
            raise BackupArchiveError("Backup manifest contains an invalid file entry")
        relative = item.get("path")
        _safe_relative_path(relative)
        if relative in seen:
            raise BackupArchiveError("Backup manifest contains duplicate paths")
        seen.add(relative)
        size = item.get("size_bytes")
        digest = item.get("sha256")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise BackupArchiveError("Backup manifest contains an invalid file size")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise BackupArchiveError("Backup manifest contains an invalid SHA-256 digest")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise BackupArchiveError("Backup manifest exceeds the configured size limit")
    return manifest


def create_archive(staging_directory: str | Path, archive_path: str | Path) -> Path:
    """Create and verify a gzip-compressed tar archive from a manifested staging tree."""
    root = Path(staging_directory).resolve()
    destination = Path(archive_path).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise BackupArchiveError("Backup destination already exists")
    if destination == root or root in destination.parents:
        raise BackupArchiveError("Backup destination must be outside the staging directory")
    manifest_path = root / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise BackupArchiveError("Backup manifest is missing")
    _verify_directory(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".opensigweave-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
        with tarfile.open(temporary, mode="w:gz") as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    archive.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
        verify_archive(temporary)
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
    except BackupArchiveError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise BackupArchiveError("Unable to create backup archive") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def _verify_directory(root: Path) -> dict[str, Any]:
    manifest_path = root / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise BackupArchiveError("Backup manifest is missing or unsafe")
    manifest = _validate_manifest(manifest_path.read_bytes())
    expected = {item["path"]: item for item in manifest["files"]}
    actual = {item["path"]: item for item in _inventory(root)}
    if expected.keys() != actual.keys():
        raise BackupArchiveError("Backup payload does not match its manifest")
    for name, item in expected.items():
        found = actual[name]
        if item["size_bytes"] != found["size_bytes"] or item["sha256"] != found["sha256"]:
            raise BackupArchiveError(f"Backup payload integrity check failed: {name}")
    return manifest


def verify_archive(archive_path: str | Path) -> dict[str, Any]:
    """Validate archive members, limits, manifest, and every payload checksum."""
    source = Path(archive_path)
    try:
        with tarfile.open(source, mode="r:gz") as archive:
            members = archive.getmembers()
            if len(members) > MAX_FILES + 1:
                raise BackupArchiveError("Backup archive contains too many entries")
            files: dict[str, tarfile.TarInfo] = {}
            total = 0
            for member in members:
                if not member.isfile():
                    raise BackupArchiveError("Backup archive may contain regular files only")
                name = member.name
                if name in files:
                    raise BackupArchiveError("Backup archive contains duplicate paths")
                if name == MANIFEST_NAME:
                    pass
                else:
                    _safe_relative_path(name)
                if member.size < 0:
                    raise BackupArchiveError("Backup archive contains an invalid file size")
                total += member.size
                if total > MAX_TOTAL_BYTES:
                    raise BackupArchiveError("Backup archive exceeds the configured size limit")
                files[name] = member
            if MANIFEST_NAME not in files:
                raise BackupArchiveError("Backup manifest is missing")
            manifest_stream = archive.extractfile(files[MANIFEST_NAME])
            if manifest_stream is None:
                raise BackupArchiveError("Backup manifest cannot be read")
            manifest = _validate_manifest(manifest_stream.read(4 * 1024 * 1024))
            expected = {item["path"]: item for item in manifest["files"]}
            if expected.keys() != (files.keys() - {MANIFEST_NAME}):
                raise BackupArchiveError("Backup archive contents do not match the manifest")
            for name, item in expected.items():
                stream = archive.extractfile(files[name])
                if stream is None:
                    raise BackupArchiveError(f"Backup payload cannot be read: {name}")
                digest = hashlib.sha256()
                size = 0
                for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
                    size += len(chunk)
                    digest.update(chunk)
                if size != item["size_bytes"] or digest.hexdigest() != item["sha256"]:
                    raise BackupArchiveError(f"Backup payload integrity check failed: {name}")
            return manifest
    except BackupArchiveError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise BackupArchiveError("Backup archive is unreadable") from exc


def extract_archive(archive_path: str | Path, destination: str | Path) -> dict[str, Any]:
    """Verify first, then safely extract regular files into a new empty directory."""
    manifest = verify_archive(archive_path)
    target = Path(destination).expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise BackupArchiveError("Restore destination must not already exist")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(mode=0o700)
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            for member in archive.getmembers():
                relative = PurePosixPath(member.name)
                output = target.joinpath(*relative.parts)
                output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                current = target
                for part in relative.parts[:-1]:
                    current = current / part
                    if current.is_symlink() or not current.is_dir():
                        raise BackupArchiveError("Restore path contains an unsafe directory")
                descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                stream = archive.extractfile(member)
                if stream is None:
                    os.close(descriptor)
                    raise BackupArchiveError("Backup member cannot be read during restore")
                with os.fdopen(descriptor, "wb") as destination_stream:
                    shutil.copyfileobj(stream, destination_stream, length=CHUNK_SIZE)
            return manifest
    except BackupArchiveError:
        shutil.rmtree(target, ignore_errors=True)
        raise
    except (OSError, tarfile.TarError) as exc:
        shutil.rmtree(target, ignore_errors=True)
        raise BackupArchiveError("Unable to safely extract backup archive") from exc
