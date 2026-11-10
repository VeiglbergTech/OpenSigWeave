"""Hybrid signature HTML file storage with integrity verification."""
from dataclasses import dataclass
import hashlib
import hmac
import os
from pathlib import Path
import tempfile
import uuid


class SignatureStorageError(Exception):
    """Raised when signature file storage or integrity validation fails."""


@dataclass(frozen=True)
class StoredSignature:
    relative_path: str
    sha256: str
    size_bytes: int


class SignatureFileStore:
    """Store immutable signature versions beneath a configured root directory."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    @staticmethod
    def _filename(version_id: uuid.UUID) -> str:
        if not isinstance(version_id, uuid.UUID):
            raise TypeError("version_id must be a UUID")
        return f"{version_id.hex}.html"

    def write(self, version_id: uuid.UUID, html_content: str) -> StoredSignature:
        if not isinstance(html_content, str):
            raise TypeError("html_content must be a string")
        filename = self._filename(version_id)
        payload = html_content.encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".opensigweave-",
                suffix=".tmp",
                dir=self.root,
                delete=False,
            ) as temporary:
                temp_path = Path(temporary.name)
                os.chmod(temp_path, 0o600)
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            try:
                # A hard link publishes the complete temporary file atomically
                # and fails if this immutable version ID already exists.
                os.link(temp_path, self.root / filename)
            except FileExistsError as exc:
                raise SignatureStorageError("Signature version already exists") from exc
        except OSError as exc:
            raise SignatureStorageError("Unable to persist signature version") from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

        return StoredSignature(
            relative_path=filename,
            sha256=digest,
            size_bytes=len(payload),
        )

    def read(self, version_id: uuid.UUID, expected_sha256: str) -> str:
        filename = self._filename(version_id)
        path = (self.root / filename).resolve()
        if path.parent != self.root:
            raise SignatureStorageError("Signature path escapes configured storage root")
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise SignatureStorageError("Signature version is unavailable") from exc

        actual_sha256 = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(actual_sha256, expected_sha256):
            raise SignatureStorageError("Signature version integrity check failed")
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SignatureStorageError("Signature version is not valid UTF-8") from exc

    def delete(self, version_id: uuid.UUID) -> None:
        filename = self._filename(version_id)
        path = (self.root / filename).resolve()
        if path.parent != self.root:
            raise SignatureStorageError("Signature path escapes configured storage root")
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise SignatureStorageError("Unable to delete signature version") from exc
