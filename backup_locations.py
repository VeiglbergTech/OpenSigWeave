"""Backup-location management. This does not bypass the host's age backup operator.

The location registry deliberately stores no age private keys and does not run
mount, shell, or Docker commands. Existing backup creation/restore is unchanged.
"""
from __future__ import annotations

import os
import re
import socket
import stat
import uuid
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class BackupLocationError(Exception):
    def __init__(self, detail: str, status: int = 422):
        super().__init__(detail)
        self.status = status


def _cipher() -> Fernet:
    key = os.environ.get("BACKUP_LOCATIONS_KEY", "").strip()
    if not key or key.startswith("REPLACE_"):
        raise BackupLocationError(
            "SMB credential encryption is not configured. Set BACKUP_LOCATIONS_KEY "
            "to a separately generated Fernet key in the protected application .env.", 503
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise BackupLocationError("BACKUP_LOCATIONS_KEY is not a valid Fernet key.", 503) from None


def encrypt_password(password: str) -> str:
    if not password or len(password) > 4096:
        raise BackupLocationError("SMB password must contain 1–4096 characters.")
    return _cipher().encrypt(password.encode("utf-8")).decode("ascii")


def decrypt_password(token: str) -> str:
    try:
        return _cipher().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        raise BackupLocationError(
            "Stored SMB credential could not be decrypted. Check BACKUP_LOCATIONS_KEY.", 503
        ) from None


def _validated(label: str, value: str, pattern: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or not re.fullmatch(pattern, value):
        raise BackupLocationError("Invalid " + label + ".")
    return value


def validate_subdirectory(value: str | None) -> str:
    if not value:
        return ""
    if not isinstance(value, str) or len(value) > 512 or "\\" in value or value.startswith("/"):
        raise BackupLocationError("SMB subdirectory must be a relative path using '/' separators.")
    segments = value.split("/")
    if (len(segments) > 32 or any(
        part in ("", ".", "..") or not re.fullmatch(r"[A-Za-z0-9 _().-]{1,100}", part)
        or part.endswith((" ", ".")) for part in segments
    )):
        raise BackupLocationError("SMB subdirectory contains an unsafe path component.")
    return "/".join(segments)


def apply_location(row, data: dict, *, creating: bool = False) -> None:
    name = data.get("name", "").strip()
    if not name or len(name) > 120 or any(ord(char) < 32 for char in name):
        raise BackupLocationError("Name must contain 1–120 printable characters.")
    kind = data.get("type")
    if kind not in ("local", "smb"):
        raise BackupLocationError("Backup location type must be Local or SMB.")
    if not isinstance(data.get("enabled", True), bool):
        raise BackupLocationError("Enabled must be a boolean.")

    if kind == "smb":
        server = _validated("SMB server", data.get("server", "").strip(),
                            r"[A-Za-z0-9][A-Za-z0-9_.-]*", 253)
        share = _validated("SMB share", data.get("share", "").strip(),
                           r"[A-Za-z0-9][A-Za-z0-9 _.$-]*", 80)
        if share in (".", "..") or share.endswith((" ", ".")):
            raise BackupLocationError("Invalid SMB share.")
        username = _validated("SMB username", data.get("username", "").strip(),
                              r"[A-Za-z0-9_.@+-]+", 255)
        domain = (data.get("domain") or "").strip()
        if domain:
            domain = _validated("SMB domain", domain, r"[A-Za-z0-9_.-]+", 128)
        directory = validate_subdirectory((data.get("directory") or "").strip())
        secret = data.get("password")
        if secret is not None and not isinstance(secret, str):
            raise BackupLocationError("Invalid SMB password.")
        if secret:
            ciphertext = encrypt_password(secret)
        elif creating or row.type != "smb" or not row.password_ciphertext:
            raise BackupLocationError("SMB password is required.")
        else:
            ciphertext = row.password_ciphertext
        row.server, row.share, row.directory = server, share, directory
        row.username, row.domain, row.password_ciphertext = username, domain, ciphertext
    else:
        # Clearing credentials when switching to Local prevents unused secrets
        # from remaining in the database. Nothing in Local is browser-selectable.
        row.server = row.share = row.directory = row.username = row.domain = None
        row.password_ciphertext = None
    row.name, row.type, row.is_enabled = name, kind, data.get("enabled", True)


def local_root() -> Path:
    return Path(os.environ.get("BACKUP_LOCAL_ROOT", str(Path(__file__).resolve().parent / "backups"))).absolute()


def ensure_local_directory(location_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", location_id):
        raise BackupLocationError("Invalid local backup location ID.")
    root = local_root()
    try:
        if root.is_symlink():
            raise BackupLocationError("Local backup root must not be a symbolic link.", 503)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        root_fd = os.open(root, flags)
        try:
            root_stat = os.fstat(root_fd)
            if root_stat.st_uid != os.geteuid() or root_stat.st_mode & 0o077:
                raise BackupLocationError("Local backup root must be owned by the application and mode 0700.", 503)
            try:
                os.mkdir(location_id, mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            child_fd = os.open(location_id, flags, dir_fd=root_fd)
            try:
                child_stat = os.fstat(child_fd)
                if child_stat.st_uid != os.geteuid() or child_stat.st_mode & 0o077:
                    raise BackupLocationError("Local backup directory has unsafe permissions.", 503)
            finally:
                os.close(child_fd)
        finally:
            os.close(root_fd)
    except OSError:
        raise BackupLocationError("Local backup directory cannot be created or accessed safely.", 503) from None
    return root / location_id


def public_location(row) -> dict:
    # Never include the password or ciphertext in any serialized response.
    return {
        "id": row.id, "name": row.name, "type": row.type,
        "enabled": bool(row.is_enabled),
        "server": row.server or "", "share": row.share or "",
        "directory": row.directory or "", "username": row.username or "",
        "domain": row.domain or "",
        "has_password": bool(row.password_ciphertext),
        "local_path": str(local_root() / row.id) if row.type == "local" else None,
    }


def _smb_unc(row) -> str:
    # All fields have already been validated and cannot contain separators.
    root = "\\\\" + row.server + "\\" + row.share
    if row.directory:
        root += "\\" + row.directory.replace("/", "\\")
    return root


def test_location(row) -> dict:
    if row.type == "local":
        target = ensure_local_directory(row.id)
        # Probe this directory only, without following links.
        try:
            fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                marker = ".osw-probe-" + uuid.uuid4().hex
                probe_fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                   0o600, dir_fd=fd)
                os.close(probe_fd)
                os.unlink(marker, dir_fd=fd)
            finally:
                os.close(fd)
        except OSError:
            raise BackupLocationError("Local backup directory is not writable.", 503) from None
        return {"ok": True, "detail": "Local directory is writable."}

    if row.type != "smb" or not row.password_ciphertext:
        raise BackupLocationError("SMB location has no saved credentials.", 422)
    password = decrypt_password(row.password_ciphertext)
    import smbclient
    path = _smb_unc(row)
    username = (row.domain + "\\" if row.domain else "") + row.username
    probe = path + "\\.osw-probe-" + uuid.uuid4().hex
    created = False
    try:
        # SMB3 encryption and signing are mandatory; no system mounts or shell.
        smbclient.register_session(
            row.server, username=username, password=password,
            encrypt=True, require_signing=True, connection_timeout=8,
        )
        smbclient.listdir(path)
        with smbclient.open_file(probe, mode="xb") as stream:
            created = True
            stream.write(b"OpenSigWeave write probe\n")
        smbclient.remove(probe)
        created = False
        return {"ok": True, "detail": "SMB3 connection and write access verified."}
    except Exception as exc:
        kind = type(exc).__name__.lower()
        if any(word in kind for word in ("auth", "logon", "accessdenied", "permission")):
            detail = "SMB authentication failed or the account lacks share permissions."
        elif "timeout" in kind or isinstance(exc, (socket.timeout, TimeoutError)):
            detail = "SMB connection timed out. Check TCP/445 and server reachability."
        elif any(word in kind for word in ("notfound", "pathnotfound", "badnetworkname", "objectname")):
            detail = "SMB share or subdirectory does not exist."
        else:
            detail = "SMB connection or write test failed. Check SMB3 support, server, share and permissions."
        # Do not stringify the exception: libraries may include credentials.
        raise BackupLocationError(detail, 502) from None
    finally:
        if created:
            try:
                smbclient.remove(probe)
            except Exception:
                pass
