"""Safe retention for verified encrypted OpenSigWeave backups."""
from __future__ import annotations

import os
from pathlib import Path

from backup_encryption import BackupEncryptionError, decrypt_and_verify_archive


class BackupRetentionError(Exception):
    """Raised when backup retention cannot be completed safely."""


def prune_backups(
    directory: str | Path,
    identity_file: str | Path,
    *,
    keep: int = 7,
    apply: bool = False,
    age_binary: str = "age",
) -> dict[str, list[Path]]:
    """Plan or apply retention after verifying every candidate archive.

    Only direct, regular .age files are considered. The newest keep verified
    archives are retained. Invalid archives abort before any deletion.
    Without apply=True this function is strictly read-only.
    """
    root = Path(directory).expanduser().absolute()
    identity = Path(identity_file).expanduser().absolute()
    if isinstance(keep, bool) or keep < 1:
        raise BackupRetentionError("keep must be at least 1")
    if root.is_symlink() or not root.is_dir():
        raise BackupRetentionError("Backup directory must be an existing real directory")
    if identity.is_symlink() or not identity.is_file():
        raise BackupRetentionError("The age identity file is missing or unsafe")

    candidates: list[tuple[Path, os.stat_result]] = []
    try:
        for path in root.iterdir():
            if path.suffix != ".age" or path.name.startswith("."):
                continue
            info = path.lstat()
            if path.is_symlink() or not path.is_file():
                raise BackupRetentionError(f"Refusing non-regular backup entry: {path.name}")
            candidates.append((path, info))
    except OSError as exc:
        raise BackupRetentionError("Unable to inspect backup directory") from exc

    candidates.sort(key=lambda item: (item[1].st_mtime_ns, item[0].name), reverse=True)
    verified: list[tuple[Path, os.stat_result]] = []
    for path, original_info in candidates:
        try:
            current_info = path.lstat()
            if (
                path.is_symlink()
                or not path.is_file()
                or (current_info.st_dev, current_info.st_ino, current_info.st_size, current_info.st_mtime_ns)
                != (original_info.st_dev, original_info.st_ino, original_info.st_size, original_info.st_mtime_ns)
            ):
                raise BackupRetentionError(f"Backup changed during inspection: {path.name}")
            decrypt_and_verify_archive(path, identity, executable=age_binary)
            verified.append((path, original_info))
        except BackupEncryptionError as exc:
            raise BackupRetentionError(f"Backup verification failed; nothing was deleted: {path.name}") from exc

    retained = [item[0] for item in verified[:keep]]
    proposed = verified[keep:]
    deleted: list[Path] = []
    if apply:
        for path, original_info in proposed:
            try:
                current_info = path.lstat()
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or (current_info.st_dev, current_info.st_ino, current_info.st_size, current_info.st_mtime_ns)
                    != (original_info.st_dev, original_info.st_ino, original_info.st_size, original_info.st_mtime_ns)
                ):
                    raise BackupRetentionError(f"Backup changed before deletion: {path.name}")
                path.unlink()
                deleted.append(path)
            except OSError as exc:
                raise BackupRetentionError(
                    f"Could not delete backup {path.name}; review the directory before retrying"
                ) from exc

    return {
        "retained": retained,
        "would_delete": [item[0] for item in proposed],
        "deleted": deleted,
    }
