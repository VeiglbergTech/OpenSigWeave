"""Authenticated encryption helpers for OpenSigWeave backup archives.

The age executable is deliberately invoked as an external, well-known tool.
Private identities are supplied by the operator and are never copied into a
backup or logged.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile

from backup_archive import BackupArchiveError, verify_archive


class BackupEncryptionError(Exception):
    """Raised when age encryption or decryption fails."""


def _run_age(
    arguments: list[str],
    *,
    output_path: Path,
    timeout: int,
    executable: str,
) -> None:
    try:
        # Stream binary output to a pre-created 0600 temporary file. Using
        # age's --output with an existing tempfile can trigger no-overwrite.
        with output_path.open("wb") as output:
            result = subprocess.run(
                [executable, *arguments],
                check=False,
                stdout=output,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
            )
    except FileNotFoundError as exc:
        raise BackupEncryptionError("The age executable is not installed or not in PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise BackupEncryptionError("The age operation exceeded its time limit") from exc
    if result.returncode != 0:
        # age output is intentionally not included: it may contain sensitive paths.
        raise BackupEncryptionError("The age operation failed; verify the recipient or identity")


def encrypt_archive(
    archive_path: str | Path,
    encrypted_path: str | Path,
    recipient: str,
    *,
    executable: str = "age",
    timeout: int = 900,
) -> Path:
    """Encrypt a verified archive for an age recipient and atomically publish it."""
    source = Path(archive_path)
    destination = Path(encrypted_path).expanduser().absolute()
    if not recipient or not recipient.strip():
        raise BackupEncryptionError("An age recipient is required")
    if source.is_symlink() or not source.is_file():
        raise BackupEncryptionError("The plaintext archive must be a regular file")
    try:
        verify_archive(source)
    except BackupArchiveError as exc:
        raise BackupEncryptionError("Refusing to encrypt an invalid backup archive") from exc
    if destination.exists() or destination.is_symlink():
        raise BackupEncryptionError("Encrypted backup destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=".opensigweave-age-", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
        os.chmod(temporary, 0o600)
        _run_age(
            ["--encrypt", "--recipient", recipient, str(source)],
            output_path=temporary,
            timeout=timeout,
            executable=executable,
        )
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise BackupEncryptionError("age did not produce an encrypted backup")
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
        return destination
    except OSError as exc:
        raise BackupEncryptionError("Unable to publish encrypted backup") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def decrypt_and_verify_archive(
    encrypted_path: str | Path,
    identity_file: str | Path,
    *,
    executable: str = "age",
    timeout: int = 900,
) -> dict:
    """Decrypt into a private temporary file, verify every checksum, then erase it."""
    source = Path(encrypted_path)
    identity = Path(identity_file)
    if source.is_symlink() or not source.is_file():
        raise BackupEncryptionError("Encrypted backup must be a regular file")
    if identity.is_symlink() or not identity.is_file():
        raise BackupEncryptionError("The age identity file is missing or unsafe")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="opensigweave-verify-", suffix=".tar.gz", delete=False) as stream:
            temporary = Path(stream.name)
        os.chmod(temporary, 0o600)
        _run_age(
            ["--decrypt", "--identity", str(identity), str(source)],
            output_path=temporary,
            timeout=timeout,
            executable=executable,
        )
        try:
            return verify_archive(temporary)
        except BackupArchiveError as exc:
            raise BackupEncryptionError("Decrypted backup failed integrity verification") from exc
    except OSError as exc:
        raise BackupEncryptionError("Unable to verify encrypted backup") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def decrypt_archive(
    encrypted_path: str | Path,
    identity_file: str | Path,
    destination: str | Path,
    *,
    executable: str = "age",
    timeout: int = 900,
) -> Path:
    """Decrypt and verify an archive before atomically publishing plaintext output."""
    source = Path(encrypted_path)
    identity = Path(identity_file)
    target = Path(destination).expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise BackupEncryptionError("Decrypted archive destination already exists")
    if source.is_symlink() or not source.is_file() or identity.is_symlink() or not identity.is_file():
        raise BackupEncryptionError("Encrypted backup or age identity is missing or unsafe")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=".opensigweave-decrypt-", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
        os.chmod(temporary, 0o600)
        _run_age(
            ["--decrypt", "--identity", str(identity), str(source)],
            output_path=temporary,
            timeout=timeout,
            executable=executable,
        )
        try:
            verify_archive(temporary)
        except BackupArchiveError as exc:
            raise BackupEncryptionError("Decrypted backup failed integrity verification") from exc
        os.replace(temporary, target)
        os.chmod(target, 0o600)
        return target
    except OSError as exc:
        raise BackupEncryptionError("Unable to publish decrypted backup") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def encrypt_file(
    source_path: str | Path,
    encrypted_path: str | Path,
    recipient: str,
    *,
    executable: str = "age",
    timeout: int = 900,
) -> Path:
    """Encrypt one regular file without placing plaintext in a backup staging tree."""
    source = Path(source_path)
    destination = Path(encrypted_path).expanduser().absolute()
    if not recipient or not recipient.strip():
        raise BackupEncryptionError("An age recipient is required")
    if source.is_symlink() or not source.is_file():
        raise BackupEncryptionError("Encryption source must be a regular file")
    if destination.exists() or destination.is_symlink():
        raise BackupEncryptionError("Encrypted file destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=".opensigweave-age-", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
        os.chmod(temporary, 0o600)
        _run_age(
            ["--encrypt", "--recipient", recipient, str(source)],
            output_path=temporary,
            timeout=timeout,
            executable=executable,
        )
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise BackupEncryptionError("age did not produce encrypted output")
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
        return destination
    except OSError as exc:
        raise BackupEncryptionError("Unable to publish encrypted file") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)



def decrypt_file(
    encrypted_path: str | Path,
    identity_file: str | Path,
    destination: str | Path,
    *,
    executable: str = "age",
    timeout: int = 900,
) -> Path:
    """Decrypt one age-encrypted regular file to a new private destination."""
    source = Path(encrypted_path)
    identity = Path(identity_file)
    target = Path(destination).expanduser().absolute()
    if source.is_symlink() or not source.is_file():
        raise BackupEncryptionError("Encrypted file is missing or unsafe")
    if identity.is_symlink() or not identity.is_file():
        raise BackupEncryptionError("The age identity file is missing or unsafe")
    if target.exists() or target.is_symlink():
        raise BackupEncryptionError("Decrypted file destination already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=".opensigweave-file-", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
        os.chmod(temporary, 0o600)
        _run_age(
            ["--decrypt", "--identity", str(identity), str(source)],
            output_path=temporary,
            timeout=timeout,
            executable=executable,
        )
        if not temporary.is_file():
            raise BackupEncryptionError("age did not produce decrypted output")
        os.replace(temporary, target)
        os.chmod(target, 0o600)
        return target
    except OSError as exc:
        raise BackupEncryptionError("Unable to publish decrypted file") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
