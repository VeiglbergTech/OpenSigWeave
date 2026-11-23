"""Lazily provision an age recovery identity once an actual backup is requested.

A private identity resides in the protected persistent backup volume and is
NEVER included in the application-profile backup archive. Administrators must
export a separate offline copy before relying on the recovery procedure.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import re
import stat
import subprocess

IDENTITY_RELATIVE_PATH = ".recovery/age-identity.txt"


class RecoveryKeyError(Exception):
    pass


def _age(*args: str) -> str:
    try:
        result = subprocess.run(
            ["age-keygen", *args], capture_output=True, text=True,
            check=False, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RecoveryKeyError("age-keygen is not available in the application container") from None
    if result.returncode:
        raise RecoveryKeyError("age-keygen failed; check backup volume permissions")
    return result.stdout


def automatic_identity(work_root: Path) -> Path:
    """Generate only when missing, under an exclusive filesystem lock."""
    root = work_root / ".recovery"
    if root.is_symlink():
        raise RecoveryKeyError("Unsafe recovery key directory")
    root.mkdir(mode=0o700, exist_ok=True)
    meta = root.stat()
    if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.geteuid() or stat.S_IMODE(meta.st_mode) & 0o077:
        raise RecoveryKeyError("Recovery key directory must be private (0700)")
    fd = os.open(root / ".keygen.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        path = root / "age-identity.txt"
        if path.is_symlink():
            raise RecoveryKeyError("Unsafe recovery identity symlink")
        if not path.exists():
            private = _age()
            if not re.search(r"(?m)^AGE-SECRET-KEY-[A-Z0-9]+$", private):
                raise RecoveryKeyError("age-keygen returned an invalid recovery identity")
            key_fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            try:
                with os.fdopen(key_fd, "w", encoding="ascii") as stream:
                    stream.write(private)
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                path.unlink(missing_ok=True)
                raise
        metadata = path.stat()
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077):
            raise RecoveryKeyError("Recovery identity must be owner-only (0600)")
        return path
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def recipient_for_backup(work_root: Path) -> str:
    recipient = os.environ.get("BACKUP_AGE_RECIPIENT", "").strip()
    inline = os.environ.get("BACKUP_AGE_IDENTITY", "").strip()
    file = os.environ.get("BACKUP_AGE_IDENTITY_FILE", "").strip()
    if recipient or inline or file:
        if (not recipient or not recipient.startswith("age1") or len(recipient) > 200
                or bool(inline) == bool(file)):
            raise RecoveryKeyError("Incomplete age configuration; supply recipient and exactly one identity source")
        return recipient
    path = automatic_identity(work_root)
    public = _age("-y", str(path)).strip()
    if not public.startswith("age1") or len(public) > 200:
        raise RecoveryKeyError("Unable to derive age public recipient from recovery identity")
    return public


def automatic_identity_if_configured(work_root: Path) -> Path | None:
    """Return the automatic key only when no legacy age settings are in use."""
    if any(os.environ.get(key, "").strip() for key in (
        "BACKUP_AGE_RECIPIENT", "BACKUP_AGE_IDENTITY", "BACKUP_AGE_IDENTITY_FILE"
    )):
        return None
    return automatic_identity(work_root)
