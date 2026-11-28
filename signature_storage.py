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
import shutil
import time


class SignatureStorageError(Exception):
    """Raised when signature file storage or integrity validation fails."""


MAX_SIGNATURE_BYTES = 65536
MAX_SIGNATURE_IMAGE_BYTES = 12 * 1024 * 1024  # Hard ceiling for existing asset retrieval
DEFAULT_SIGNATURE_IMAGE_BYTES = 6 * 1024 * 1024
_SIGNATURE_IMAGE_TYPES = {
    "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": (".jpg", b"\xff\xd8\xff"),
    "image/webp": (".webp", b"RIFF"),
    "image/gif": (".gif", (b"GIF87a", b"GIF89a")),
}


@dataclass(frozen=True)
class StoredSignature:
    relative_path: str
    sha256: str
    size_bytes: int


class SignatureFileStore:
    """Store signature HTML snapshots and immutable shared signature image assets."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.root.is_dir():
            raise SignatureStorageError("Signature storage root must be a directory")
        os.chmod(self.root, 0o700)
        self.signatures_root = self.root / "signatur"
        self.signatures_root.mkdir(mode=0o700, exist_ok=True)
        if self.signatures_root.is_symlink() or not self.signatures_root.is_dir():
            raise SignatureStorageError("Signature directory root must be a real directory")
        self.assets_root = self.root / "assets"
        self.assets_root.mkdir(mode=0o700, exist_ok=True)
        if self.assets_root.is_symlink() or not self.assets_root.is_dir():
            raise SignatureStorageError("Signature asset directory root must be a real directory")
        self.cleanup_staged_signature_deletes()

    @staticmethod
    def _safe_name(name: str) -> str:
        if not isinstance(name, str):
            raise TypeError("signature_name must be a string")
        name = name.strip()
        if (not name or name in {".", ".."} or name[-1:] in {".", " "}
                or re.search(r'[\\/\x00-\x1f\x7f]', name)
                or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", name, re.I)):
            raise SignatureStorageError("Signature name is not a safe directory name")
        if len(name.encode("utf-8")) > 198:
            raise SignatureStorageError("Signature name is too long for filesystem storage")
        return name

    def _signature_dir(self, signature_name: str) -> Path:
        name = self._safe_name(signature_name)
        path = self.signatures_root / name
        if path.parent != self.signatures_root:
            raise SignatureStorageError("Invalid signature directory")
        return path

    def ensure_name_available(self, signature_name: str) -> None:
        path = self._signature_dir(signature_name)
        if path.exists() or path.is_symlink():
            raise SignatureStorageError("A signature with this name already has a storage directory")
        normalized_name = self._safe_name(signature_name).casefold()
        if any(
            entry.name.casefold() == normalized_name
            for entry in self.signatures_root.iterdir()
        ):
            raise SignatureStorageError("A signature with this name already has a storage directory")

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
        if signature_dir.is_symlink() or (signature_dir.exists() and not signature_dir.is_dir()):
            raise SignatureStorageError("Signature directory is unavailable or unsafe")
        signature_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(signature_dir, 0o700)
        payload = html_content.encode("utf-8")
        if len(payload) > MAX_SIGNATURE_BYTES:
            raise SignatureStorageError("Signature asset exceeds the maximum allowed size")
        digest = hashlib.sha256(payload).hexdigest()
        versions_dir = signature_dir / ".versions"
        if versions_dir.is_symlink() or (versions_dir.exists() and not versions_dir.is_dir()):
            raise SignatureStorageError("Signature versions directory is unsafe")
        versions_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(versions_dir, 0o700)
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
        signature_dir = self._signature_dir(signature_name)
        versions_dir = signature_dir / ".versions"
        if signature_dir.is_symlink() or versions_dir.is_symlink():
            raise SignatureStorageError("Signature directory is unsafe")
        path = versions_dir / filename
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
        signature_dir = self._signature_dir(signature_name)
        versions_dir = signature_dir / ".versions"
        if signature_dir.is_symlink() or versions_dir.is_symlink():
            raise SignatureStorageError("Signature directory is unsafe")
        path = versions_dir / self._version_filename(version_id)
        try:
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise SignatureStorageError("Refusing to delete a non-regular signature asset")
            path.unlink()
            versions_dir = path.parent
            if versions_dir.exists() and not any(versions_dir.iterdir()):
                versions_dir.rmdir()
                canonical = versions_dir.parent / "signature.html"
                if canonical.is_file() and not canonical.is_symlink():
                    canonical.unlink()
                versions_dir.parent.rmdir()
        except FileNotFoundError:
            return
        except SignatureStorageError:
            raise
        except OSError as exc:
            raise SignatureStorageError("Unable to delete signature version") from exc


    # A same-filesystem directory rename is the only atomic boundary available
    # for coordinating SQL metadata deletion with HTML files. Shared image
    # assets are not owned by an individual signature and are never touched.
    _DELETED_NAME = re.compile(r"\.deleted-[0-9a-f]{32}\Z")

    def stage_signature_delete(self, signature_name: str) -> str:
        source = self._signature_dir(signature_name)
        if source.is_symlink() or not source.is_dir():
            raise SignatureStorageError("Signature directory is unavailable or unsafe")
        token = ".deleted-" + uuid.uuid4().hex
        destination = self.signatures_root / token
        if destination.exists() or destination.is_symlink():
            raise SignatureStorageError("Signature delete staging collision")
        try:
            os.rename(source, destination)
            os.utime(destination, None)  # age is measured from staging, not creation
        except OSError as exc:
            if destination.is_dir() and not destination.is_symlink() and not source.exists():
                try:
                    os.rename(destination, source)
                except OSError:
                    pass
            raise SignatureStorageError("Unable to stage signature for deletion") from exc
        return token

    def _staged_signature_dir(self, token: str) -> Path:
        if not isinstance(token, str) or not self._DELETED_NAME.fullmatch(token):
            raise SignatureStorageError("Invalid staged signature identifier")
        path = self.signatures_root / token
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise SignatureStorageError("Staged signature directory is unsafe")
        return path

    def restore_staged_signature(self, signature_name: str, token: str) -> None:
        source = self._staged_signature_dir(token)
        destination = self._signature_dir(signature_name)
        if not source.is_dir() or destination.exists() or destination.is_symlink():
            raise SignatureStorageError("Unable to restore staged signature")
        try:
            os.rename(source, destination)
        except OSError as exc:
            raise SignatureStorageError("Unable to restore staged signature") from exc

    def purge_staged_signature(self, token: str) -> None:
        path = self._staged_signature_dir(token)
        if not path.exists():
            return
        try:
            shutil.rmtree(path)
        except OSError as exc:
            raise SignatureStorageError("Unable to purge staged signature") from exc

    def cleanup_staged_signature_deletes(self) -> None:
        """Retry post-commit cleanup after a crash; avoid in-flight recent stages."""
        for entry in self.signatures_root.iterdir():
            if not self._DELETED_NAME.fullmatch(entry.name) or entry.is_symlink():
                continue
            try:
                if time.time() - entry.stat().st_mtime >= 600:
                    self.purge_staged_signature(entry.name)
            except (OSError, SignatureStorageError):
                # Cleanup must not prevent the application from starting.
                pass

    def write_image(self, payload: bytes, content_type: str, *, max_bytes: int = DEFAULT_SIGNATURE_IMAGE_BYTES) -> str:
        """Persist a bounded immutable PNG/JPEG/GIF asset and return its hash filename."""
        if not isinstance(payload, (bytes, bytearray)):
            raise TypeError("image payload must be bytes")
        payload = bytes(payload)
        if not payload:
            raise SignatureStorageError("Signature image is empty")
        if max_bytes not in (DEFAULT_SIGNATURE_IMAGE_BYTES, MAX_SIGNATURE_IMAGE_BYTES):
            raise ValueError("Invalid image upload size limit")
        if len(payload) > max_bytes:
            raise SignatureStorageError("Signature image exceeds the maximum allowed size")

        normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
        image_type = _SIGNATURE_IMAGE_TYPES.get(normalized_type)
        if image_type is None:
            raise SignatureStorageError("Unsupported signature image type")
        extension, magic = image_type
        if normalized_type == "image/webp":
            # WebP is a RIFF container and must also carry the WEBP form type.
            valid_magic = (
                len(payload) >= 12
                and payload.startswith(b"RIFF")
                and payload[8:12] == b"WEBP"
            )
        else:
            valid_magic = any(payload.startswith(item) for item in magic) if isinstance(magic, tuple) else payload.startswith(magic)
        if not valid_magic:
            raise SignatureStorageError("Signature image content does not match its media type")

        digest = hashlib.sha256(payload).hexdigest()
        filename = digest + extension
        destination = self.assets_root / filename
        if destination.parent != self.assets_root:
            raise SignatureStorageError("Invalid signature image path")
        if destination.exists():
            self.image_path(filename)
            return filename
        try:
            self._atomic_write(destination, payload, exclusive=True)
        except SignatureStorageError:
            # Concurrent uploads of the same bytes share one hash filename.
            # The second link may lose the race after the existence check.
            # Reuse the winner only if a safe regular asset now exists; never
            # mask a storage failure when the expected file is absent.
            if not destination.exists():
                raise
            self.image_path(filename)
        return filename

    def image_path(self, filename: str) -> Path:
        """Resolve one immutable signature image without permitting path traversal."""
        if not isinstance(filename, str) or not re.fullmatch(r"[0-9a-f]{64}\.(?:png|jpg|webp|gif)", filename):
            raise SignatureStorageError("Invalid signature image name")
        path = self.assets_root / filename
        if path.parent != self.assets_root:
            raise SignatureStorageError("Invalid signature image path")
        try:
            info = path.lstat()
        except FileNotFoundError as exc:
            raise SignatureStorageError("Signature image not found") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise SignatureStorageError("Signature image is unavailable or unsafe")
        if info.st_size > MAX_SIGNATURE_IMAGE_BYTES:
            raise SignatureStorageError("Signature image exceeds the maximum allowed size")
        return path

    @staticmethod
    def image_media_type(filename: str) -> str:
        extension = Path(filename).suffix.lower()
        return {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}.get(extension, "application/octet-stream")

    def restore_current(self, signature_name: str, version_id: uuid.UUID, expected_sha256: str) -> None:
        content = self.read(signature_name, version_id, expected_sha256)
        signature_dir = self._signature_dir(signature_name)
        if signature_dir.is_symlink():
            raise SignatureStorageError("Signature directory is unsafe")
        self._atomic_write(signature_dir / "signature.html", content.encode("utf-8"))

    def rename_signature(self, old_name: str, new_name: str) -> None:
        old_dir = self._signature_dir(old_name)
        new_dir = self._signature_dir(new_name)
        if old_dir == new_dir:
            return
        if not old_dir.exists() or old_dir.is_symlink() or not old_dir.is_dir():
            raise SignatureStorageError("Existing signature directory is unavailable or unsafe")
        if new_dir.exists() or new_dir.is_symlink() or any(
            entry != old_dir and entry.name.casefold() == new_dir.name.casefold()
            for entry in self.signatures_root.iterdir()
        ):
            raise SignatureStorageError("A signature directory with the new name already exists")
        try:
            os.rename(old_dir, new_dir)
        except OSError as exc:
            raise SignatureStorageError("Unable to rename signature directory") from exc
