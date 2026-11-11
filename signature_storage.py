"""Name-based signature HTML storage with immutable version snapshots."""
from dataclasses import dataclass
import hashlib
import hmac
import os
from pathlib import Path
import re
import tempfile
import uuid
import stat


class SignatureStorageError(Exception):
    """Raised when signature file storage or integrity validation fails."""


MAX_SIGNATURE_BYTES = 65536


@dataclass(frozen=True)
class StoredSignature:
    relative_path: str
    sha256: str
    size_bytes: int


class SignatureFileStore:
    """Store each signature at signatur/{name}/signature.html plus immutable snapshots."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.root.is_dir():
            raise SignatureStorageError("Signature storage root must be a directory")
        os.chmod(self.root, 0o700)
        self.signatures_root = self.root / "signatur"
        self.signatures_root.mkdir(mode=0o700, exist_ok=True)

    @staticmethod
    def _safe_name(name: str) -> str:
        if not isinstance(name, str):
            raise TypeError("signature_name must be a string")
        name = name.strip()
        if (not name or name in {".", ".."} or name[-1:] in {".", " "}
                or re.search(r'[\\/\x00-\x1f\x7f]', name)
                or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", name, re.I)):
            raise SignatureStorageError("Signature name is not a safe directory name")
        if len(name.encode("utf-8")) > 180:
            raise SignatureStorageError("Signature name is too long for filesystem storage")
        return name

    def _signature_dir(self, signature_name: str) -> Path:
        name = self._safe_name(signature_name)
        path = self.signatures_root / name
        if path.parent != self.signatures_root:
            raise SignatureStorageError("Invalid signature directory")
        return path

    @staticmethod
    def _version_filename(version_id: uuid.UUID) -> str:
        if not isinstance(version_id, uuid.UUID):
            raise TypeError("version_id must be a UUID")
        return f"{version_id.hex}.html"

    def _atomic_write(self, destination: Path, payload: bytes, *, exclusive: bool = False) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.is_symlink() or (destination.exists() and not destination.is_file()):
            raise SignatureStorageError("Refusing to replace a non-regular signature asset")
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", prefix=".opensigweave-",
                    suffix=".tmp", dir=destination.parent, delete=False) as temporary:
                temp_path = Path(temporary.name)
                os.chmod(temp_path, 0o600)
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            if exclusive:
                os.link(temp_path, destination)
            else:
                os.replace(temp_path, destination)
            os.chmod(destination, 0o600)
        except FileExistsError as exc:
            raise SignatureStorageError("Signature version already exists") from exc
        except OSError as exc:
            raise SignatureStorageError("Unable to persist signature version") from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def write(self, version_id: uuid.UUID, signature_name: str, html_content: str) -> StoredSignature:
        if not isinstance(html_content, str):
            raise TypeError("html_content must be a string")
        filename = self._version_filename(version_id)
        signature_dir = self._signature_dir(signature_name)
        payload = html_content.encode("utf-8")
        if len(payload) > MAX_SIGNATURE_BYTES:
            raise SignatureStorageError("Signature asset exceeds the maximum allowed size")
        digest = hashlib.sha256(payload).hexdigest()
        versions_dir = signature_dir / ".versions"
        versions_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._atomic_write(versions_dir / filename, payload, exclusive=True)
        try:
            self._atomic_write(signature_dir / "signature.html", payload)
        except Exception:
            (versions_dir / filename).unlink(missing_ok=True)
            raise
        return StoredSignature(
            relative_path=f"signatur/{self._safe_name(signature_name)}/.versions/{filename}",
            sha256=digest,
            size_bytes=len(payload),
        )

    def read(self, signature_name: str, version_id: uuid.UUID, expected_sha256: str) -> str:
        filename = self._version_filename(version_id)
        path = self._signature_dir(signature_name) / ".versions" / filename
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    raise SignatureStorageError("Signature asset is not a regular file")
                if info.st_size > MAX_SIGNATURE_BYTES:
                    raise SignatureStorageError("Signature asset exceeds the maximum allowed size")
                with os.fdopen(descriptor, "rb", closefd=False) as asset:
                    payload = asset.read()
            finally:
                os.close(descriptor)
        except SignatureStorageError:
            raise
        except OSError as exc:
            raise SignatureStorageError("Signature version is unavailable or unsafe") from exc
        actual = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(actual, expected_sha256):
            raise SignatureStorageError("Signature version integrity check failed")
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SignatureStorageError("Signature version is not valid UTF-8") from exc

    def delete(self, signature_name: str, version_id: uuid.UUID) -> None:
        path = self._signature_dir(signature_name) / ".versions" / self._version_filename(version_id)
        try:
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise SignatureStorageError("Refusing to delete a non-regular signature asset")
            path.unlink()
        except FileNotFoundError:
            return
        except SignatureStorageError:
            raise
        except OSError as exc:
            raise SignatureStorageError("Unable to delete signature version") from exc

    def rename_signature(self, old_name: str, new_name: str) -> None:
        old_dir = self._signature_dir(old_name)
        new_dir = self._signature_dir(new_name)
        if old_dir == new_dir:
            return
        if not old_dir.exists() or old_dir.is_symlink() or not old_dir.is_dir():
            raise SignatureStorageError("Existing signature directory is unavailable or unsafe")
        if new_dir.exists() or new_dir.is_symlink():
            raise SignatureStorageError("A signature directory with the new name already exists")
        try:
            os.rename(old_dir, new_dir)
        except OSError as exc:
            raise SignatureStorageError("Unable to rename signature directory") from exc
