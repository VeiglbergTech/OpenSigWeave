#!/usr/bin/env python3
"""Restricted root-side operator for encrypted OpenSigWeave backup management.

Run on the Docker host as a dedicated systemd service. The application receives
access only to this Unix socket, never Docker or the age private identity.
"""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import socketserver
import stat
import struct
import subprocess
import sys
import tempfile
import time
import threading
import uuid


FILE_NAME = re.compile(r"opensigweave-[A-Za-z0-9_-]+\.age\Z")
DIRECTORY_NAME = re.compile(r"/[A-Za-z0-9_./-]+\Z")
REMOTE_FILESYSTEMS = {"nfs", "nfs4", "cifs", "smb3", "fuse.sshfs", "fuse.rclone"}
STATE_PATH = Path(os.environ.get("BACKUP_CONTROL_STATE", "/etc/opensigweave/backup-ui.json"))
OVERRIDE_PATH = Path(os.environ.get("BACKUP_CONTROL_OVERRIDES", "/etc/opensigweave/backup-ui.env"))
TIMER_OVERRIDE_PATH = Path("/etc/systemd/system/opensigweave-backup.timer.d/portal.conf")
DEFAULT_DIRECTORY = os.environ.get("BACKUP_DIRECTORY", "/mnt/opensigweave-backups")
ALLOWED_PARENT = Path(os.environ.get("BACKUP_ALLOWED_PARENT", "/mnt")).absolute()
SOCKET_PATH = Path(os.environ.get("BACKUP_CONTROL_SOCKET", "/run/opensigweave-backup/control.sock"))
APP_UID = int(os.environ.get("BACKUP_APP_UID", "10001"))
APP_GID = int(os.environ.get("BACKUP_APP_GID", "10001"))
PROJECT_DIR = Path(os.environ.get("PROJECT_DIR", "/opt/opensigweave")).absolute()
RESTORE_ROOT = Path(os.environ.get("BACKUP_RESTORE_ROOT", "/var/lib/opensigweave-restores")).absolute()
logger = logging.getLogger("opensigweave.backup_operator")
job_lock = threading.RLock()
current_job = {"state": "idle", "action": None, "id": None, "error": None}


class OperatorError(Exception):
    pass


def _write_private(path: Path, contents: str) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise OperatorError("Unsafe operator configuration file")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".backup-control-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as out:
            os.fchmod(out.fileno(), 0o600)
            out.write(contents)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _validated_directory(raw: str) -> Path:
    if not isinstance(raw, str) or not DIRECTORY_NAME.fullmatch(raw):
        raise OperatorError("Backup directory must be an absolute, simple filesystem path")
    path = Path(raw)
    if ".." in path.parts or path.is_symlink() or not path.is_dir() or path.resolve() != path:
        raise OperatorError("Backup directory is missing or contains an unsafe path")
    if not path.is_relative_to(ALLOWED_PARENT) or path == ALLOWED_PARENT:
        raise OperatorError("Backup directory is outside the permitted mount location")
    if not os.path.ismount(path):
        raise OperatorError("Backup directory must be a dedicated mounted remote filesystem")
    result = subprocess.run(
        ["findmnt", "-n", "-o", "FSTYPE", "--target", str(path)],
        capture_output=True, text=True, check=False, timeout=10,
    )
    allowed = set(os.environ.get("BACKUP_FS_ALLOWLIST", " ".join(sorted(REMOTE_FILESYSTEMS))).split())
    if result.returncode or result.stdout.strip() not in allowed.intersection(REMOTE_FILESYSTEMS):
        raise OperatorError("Backup directory must use an approved remote filesystem")
    return path


def _default_settings():
    return {"directory": DEFAULT_DIRECTORY, "automatic": False, "interval_hours": 24, "keep": 14}


def _validate_settings(value, *, check_mount=True):
    if not isinstance(value, dict):
        raise OperatorError("Invalid backup settings")
    if set(value) != {"directory", "automatic", "interval_hours", "keep"}:
        raise OperatorError("Unexpected backup settings")
    directory = value["directory"]
    if (not isinstance(directory, str) or not DIRECTORY_NAME.fullmatch(directory)
            or ".." in Path(directory).parts
            or not Path(directory).is_relative_to(ALLOWED_PARENT)
            or Path(directory) == ALLOWED_PARENT):
        raise OperatorError("Backup directory must be inside the permitted mount location")
    if check_mount:
        _validated_directory(directory)
    if type(value["automatic"]) is not bool:
        raise OperatorError("Automatic backup must be enabled or disabled")
    if type(value["interval_hours"]) is not int or not 1 <= value["interval_hours"] <= 720:
        raise OperatorError("Backup interval must be between 1 and 720 hours")
    if type(value["keep"]) is not int or not 1 <= value["keep"] <= 365:
        raise OperatorError("Backup retention must be between 1 and 365 copies")
    return value


def _settings():
    if STATE_PATH.is_symlink():
        raise OperatorError("Backup configuration is unsafe")
    if not STATE_PATH.exists():
        return _default_settings()
    try:
        return _validate_settings(json.loads(STATE_PATH.read_text(encoding="utf-8")), check_mount=False)
    except (OSError, ValueError, TypeError) as exc:
        raise OperatorError("Saved backup settings are invalid") from exc


def _systemctl(*args):
    result = subprocess.run(
        ["systemctl", *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        check=False, timeout=45,
    )
    if result.returncode:
        raise OperatorError("Could not apply the systemd backup schedule")


def _save_settings(data):
    settings = _validate_settings(data)
    with job_lock:
        if current_job["state"] in {"pending", "running"}:
            raise OperatorError("Settings cannot change during a backup operation")
        _write_private(OVERRIDE_PATH,
            "BACKUP_DIRECTORY=" + settings["directory"] + "\n"
            "RETENTION_COUNT=" + str(settings["keep"]) + "\n")
        _write_private(TIMER_OVERRIDE_PATH,
            "[Timer]\nOnCalendar=\nOnActiveSec=" + str(settings["interval_hours"]) +
            "h\nOnUnitActiveSec=" + str(settings["interval_hours"]) + "h\n")
        _systemctl("daemon-reload")
        if settings["automatic"]:
            _systemctl("enable", "--now", "opensigweave-backup.timer")
            _systemctl("restart", "opensigweave-backup.timer")
        else:
            _systemctl("disable", "--now", "opensigweave-backup.timer")
        _write_private(STATE_PATH, json.dumps(settings, sort_keys=True) + "\n")
    return settings


def _entries(directory):
    result = []
    for path in directory.iterdir():
        if not FILE_NAME.fullmatch(path.name):
            continue
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            continue
        result.append({
            "name": path.name,
            "size_bytes": info.st_size,
            "created_at": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
        })
    return sorted(result, key=lambda item: (item["created_at"], item["name"]), reverse=True)


def _file(name):
    if not isinstance(name, str) or not FILE_NAME.fullmatch(name):
        raise OperatorError("Invalid backup filename")
    path = _validated_directory(_settings()["directory"]) / name
    info = path.lstat() if path.exists() or path.is_symlink() else None
    if info is None or not stat.S_ISREG(info.st_mode):
        raise OperatorError("Backup does not exist")
    return path, info


def _open_backup(name):
    path, before = _file(name)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    after = os.fstat(fd)
    if not stat.S_ISREG(after.st_mode) or (after.st_ino, after.st_dev, after.st_size) != (
        before.st_ino, before.st_dev, before.st_size
    ):
        os.close(fd)
        raise OperatorError("Backup changed while being opened")
    return os.fdopen(fd, "rb"), after.st_size


def _try_lock():
    lock_path = Path(os.environ.get("LOCK_FILE", "/run/opensigweave/backup.lock"))
    if lock_path.is_symlink():
        raise OperatorError("Backup lock is unsafe")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise OperatorError("A scheduled backup is already running")
    return fd



# Rule-managed backups are first created on the HOST in an encrypted, private
# spool. The unprivileged application transfers only verified ciphertext to the
# selected Local/SMB destination after the application container has restarted.
MANAGED_SPOOL = Path(os.environ.get("BACKUP_MANAGED_SPOOL", "/var/lib/opensigweave-backup-spool")).absolute()
MANAGED_ID = re.compile(r"[a-f0-9]{32}\Z")


def _managed_dir():
    root = MANAGED_SPOOL
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise OperatorError("Managed backup spool is unsafe")
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = root.stat()
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise OperatorError("Managed backup spool must be owned by root with mode 0700")
    return root


def _managed_paths(run_id):
    if not isinstance(run_id, str) or not MANAGED_ID.fullmatch(run_id):
        raise OperatorError("Invalid managed backup run ID")
    root = _managed_dir()
    return root / ("opensigweave-" + run_id + ".age"), root / (run_id + ".json")


def _managed_record(run_id):
    archive, record = _managed_paths(run_id)
    if record.is_symlink() or not record.is_file():
        return {"state": "missing"}
    try:
        value = json.loads(record.read_text(encoding="utf-8"))
        if value.get("run_id") != run_id or value.get("state") not in {
            "pending", "running", "success", "failed"
        }:
            raise ValueError("Invalid record")
        # An operator restart cannot resume an in-memory worker. Fail closed,
        # leaving ciphertext untouched until the administrator inspects it.
        with job_lock:
            active = current_job.get("id") == run_id and current_job.get("state") in {"pending", "running"}
        if value["state"] in {"pending", "running"} and not active:
            value = {"run_id": run_id, "state": "failed", "error": "Host backup job was interrupted"}
            _write_private(record, json.dumps(value))
        return value
    except (OSError, ValueError, TypeError):
        raise OperatorError("Managed backup status is invalid") from None


def _managed_execute(run_id):
    archive, record = _managed_paths(run_id)
    with job_lock:
        current_job.update({"state": "running", "action": "managed-create", "id": run_id})
    _write_private(record, json.dumps({"run_id": run_id, "state": "running"}))
    run_lock_fd = None
    try:
        # Use the same flock as the legacy systemd timer and manual operator.
        run_lock_fd = _try_lock()
        # The caller must receive HTTP 202 before the application is stopped.
        time.sleep(2)
        if not os.environ.get("AGE_RECIPIENT") or not os.environ.get("AGE_IDENTITY"):
            raise OperatorError("Encrypted host backup configuration is incomplete")
        if archive.exists() or archive.is_symlink():
            raise OperatorError("Managed archive already exists")
        python = os.environ.get("PYTHON_BINARY", sys.executable)
        completed = subprocess.run([
            python, str(PROJECT_DIR / "backup_cli.py"), "create",
            "--project-dir", str(PROJECT_DIR), "--output", str(archive),
            "--recipient", os.environ["AGE_RECIPIENT"],
            "--identity", os.environ["AGE_IDENTITY"],
            "--docker-binary", os.environ.get("DOCKER_BINARY", "docker"),
            "--age-binary", os.environ.get("AGE_BINARY", "age"),
        ], cwd=PROJECT_DIR, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=21600, check=False)
        if completed.returncode or archive.is_symlink() or not archive.is_file():
            raise OperatorError("Encrypted backup creation or verification failed")
        import hashlib
        digest = hashlib.sha256()
        with archive.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        _write_private(record, json.dumps({
            "run_id": run_id, "state": "success", "size": archive.stat().st_size,
            "sha256": digest.hexdigest(),
        }))
        with job_lock:
            current_job.update({"state": "complete", "error": None})
    except Exception:
        # No library/subprocess exception text (including credential-bearing
        # paths) is returned to the unprivileged caller.
        archive.unlink(missing_ok=True)
        _write_private(record, json.dumps({
            "run_id": run_id, "state": "failed",
            "error": "Host could not create and verify the encrypted backup",
        }))
        with job_lock:
            current_job.update({"state": "failed", "error": "Managed backup failed"})
    finally:
        if run_lock_fd is not None:
            os.close(run_lock_fd)


def _managed_queue(run_id):
    archive, record = _managed_paths(run_id)
    with job_lock:
        existing = _managed_record(run_id)
        if existing["state"] != "missing":
            return {"job": existing}
        if current_job["state"] in {"pending", "running"}:
            raise OperatorError("A backup operation is already running")
        if not _managed_readiness()["managed_available"]:
            raise OperatorError("Protected host backup configuration is incomplete")
        # Serialize managed and legacy backup jobs with the same host lock.
        current_job.update({"state": "pending", "action": "managed-create", "id": run_id, "error": None})
        _write_private(record, json.dumps({"run_id": run_id, "state": "pending"}))
        threading.Thread(target=_managed_execute, args=(run_id,), daemon=True).start()
        return {"job": {"run_id": run_id, "state": "pending"}}


def _managed_open(run_id):
    archive, _ = _managed_paths(run_id)
    status = _managed_record(run_id)
    if status.get("state") != "success":
        raise OperatorError("Managed backup is not ready")
    if archive.is_symlink() or not archive.is_file():
        raise OperatorError("Managed encrypted archive is unavailable")
    fd = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_size != status["size"]:
        os.close(fd)
        raise OperatorError("Managed encrypted archive changed")
    return os.fdopen(fd, "rb"), info.st_size


def _managed_ack(run_id):
    archive, record = _managed_paths(run_id)
    status = _managed_record(run_id)
    if status.get("state") != "success":
        raise OperatorError("Only successful managed backups can be acknowledged")
    with job_lock:
        if current_job["state"] in {"pending", "running"}:
            raise OperatorError("A backup operation is running")
        if archive.is_symlink() or record.is_symlink():
            raise OperatorError("Unsafe managed archive")
        archive.unlink(missing_ok=True)
        record.unlink()
    return {"acknowledged": True}


def _delete(name, confirm):
    if confirm != "DELETE":
        raise OperatorError("Explicit deletion confirmation is required")
    with job_lock:
        if current_job["state"] in {"pending", "running"}:
            raise OperatorError("Backup operation in progress")
        fd = _try_lock()
        try:
            path, before = _file(name)
            current = path.lstat()
            if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size) != (
                current.st_dev, current.st_ino, current.st_mtime_ns, current.st_size
            ):
                raise OperatorError("Backup changed before deletion")
            path.unlink()
        finally:
            os.close(fd)
    return {"deleted": name}


def _available_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _execute_job(action, name, job_id):
    global current_job
    restore_lock_fd = None
    with job_lock:
        current_job = {"state": "running", "action": action, "id": job_id, "error": None}
    logger.info("backup job started: action=%s id=%s", action, job_id)
    try:
        settings = _settings()
        directory = _validated_directory(settings["directory"])
        python = os.environ.get("PYTHON_BINARY", sys.executable)
        if action == "create":
            # Allow FastAPI to flush HTTP 202 before Docker stops its container.
            time.sleep(2)
            before = {item["name"] for item in _entries(directory)}
            environment = os.environ.copy()
            environment.update({
                "BACKUP_DIRECTORY": str(directory),
                "RETENTION_COUNT": str(settings["keep"]),
                "PROJECT_DIR": str(PROJECT_DIR),
            })
            command = ["/bin/bash", str(PROJECT_DIR / "scripts" / "backup_to_mounted_storage.sh")]
            kwargs = {"env": environment}
        else:
            restore_lock_fd = _try_lock()
            _file(name)
            if RESTORE_ROOT.is_symlink() or (RESTORE_ROOT.exists() and not RESTORE_ROOT.is_dir()):
                raise OperatorError("Restore root is unsafe")
            RESTORE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
            project_name = "oswrestore" + job_id[:12]
            destination = RESTORE_ROOT / project_name
            command = [
                python, str(PROJECT_DIR / "backup_cli.py"), "restore", str(directory / name),
                "--identity", os.environ["AGE_IDENTITY"], "--target-dir", str(destination),
                "--project-name", project_name, "--app-port", str(_available_port()),
                "--docker-binary", os.environ.get("DOCKER_BINARY", "docker"),
                "--age-binary", os.environ.get("AGE_BINARY", "age"),
            ]
            kwargs = {}
        completed = subprocess.run(
            command, cwd=PROJECT_DIR, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=21600, check=False, **kwargs,
        )
        if completed.returncode:
            raise OperatorError("Host backup command failed; inspect restricted systemd logs")
        if action == "create" and not ({item["name"] for item in _entries(directory)} - before):
            raise OperatorError("No new backup was published; a concurrent backup may have run")
        with job_lock:
            current_job = {"state": "complete", "action": action, "id": job_id, "error": None,
                           "target": str(destination) if action == "restore" else None}
        logger.info("backup job completed: action=%s id=%s", action, job_id)
    except Exception as exc:
        # Do not serialize exception text from subprocesses or secret-bearing inputs.
        safe = str(exc) if isinstance(exc, OperatorError) else "Host backup operation failed"
        with job_lock:
            current_job = {"state": "failed", "action": action, "id": job_id, "error": safe}
        logger.warning("backup job failed: action=%s id=%s reason=%s", action, job_id, safe)
    finally:
        if restore_lock_fd is not None:
            os.close(restore_lock_fd)


def _queue(action, name=None, confirm=None):
    global current_job
    if action == "restore" and confirm != "RESTORE TO NEW DEPLOYMENT":
        raise OperatorError("Explicit recovery confirmation is required")
    if action == "restore":
        _file(name)
    else:
        _validated_directory(_settings()["directory"])
    with job_lock:
        if current_job["state"] in {"pending", "running"}:
            raise OperatorError("A backup operation is already running")
        identifier = uuid.uuid4().hex
        current_job = {"state": "pending", "action": action, "id": identifier, "error": None}
        thread = threading.Thread(target=_execute_job, args=(action, name, identifier), daemon=True)
        thread.start()
        return {"job": dict(current_job)}


def _managed_readiness():
    """Report mandatory host prerequisites without exposing credential values."""
    recipient = os.environ.get("AGE_RECIPIENT", "")
    identity_value = os.environ.get("AGE_IDENTITY", "")
    if not recipient or recipient.startswith("REPLACE_") or not identity_value:
        return {"managed_available": False, "managed_error": "Host age encryption settings are incomplete"}
    identity = Path(identity_value)
    try:
        info = identity.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            return {"managed_available": False, "managed_error": "Protected age identity is missing or has unsafe permissions"}
    except OSError:
        return {"managed_available": False, "managed_error": "Protected age identity is missing or has unsafe permissions"}
    if not (PROJECT_DIR / "backup_cli.py").is_file():
        return {"managed_available": False, "managed_error": "Host backup command is missing"}
    try:
        _managed_dir()
    except OperatorError:
        return {"managed_available": False, "managed_error": "Protected host backup spool is unavailable"}
    return {"managed_available": True, "managed_error": None}


def _dispatch(request):
    action = request.get("action")
    if action == "status":
        with job_lock:
            state = dict(current_job)
        try:
            settings = _settings()
        except OperatorError as exc:
            return {"available": False, "mount_ready": False, "settings": _default_settings(),
                    "backups": [], "job": state, "error": str(exc), **_managed_readiness()}
        try:
            directory = _validated_directory(settings["directory"])
            return {"available": True, "mount_ready": True, "settings": settings,
                    "backups": _entries(directory), "job": state, **_managed_readiness()}
        except OperatorError as exc:
            return {"available": True, "mount_ready": False, "settings": settings,
                    "backups": [], "job": state, "error": str(exc), **_managed_readiness()}
    if action == "managed-create":
        return _managed_queue(request.get("run_id"))
    if action == "managed-status":
        return {"job": _managed_record(request.get("run_id"))}
    if action == "managed-ack":
        return _managed_ack(request.get("run_id"))
    if action == "settings":
        return {"settings": _save_settings(request.get("settings"))}
    if action == "create":
        return _queue("create")
    if action == "restore":
        return _queue("restore", request.get("name"), request.get("confirm"))
    if action == "delete":
        return _delete(request.get("name"), request.get("confirm"))
    if action == "inspect":
        file, size = _open_backup(request.get("name"))
        file.close()
        return {"size": size}
    raise OperatorError("Unknown backup action")


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            if not hasattr(socket, "SO_PEERCRED"):
                raise OperatorError("Backup operator requires Linux peer credentials")
            _, uid, _ = struct.unpack("3i", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid not in {0, APP_UID}:
                raise OperatorError("Backup access denied")
            message = self.rfile.readline(4097)
            if not message or len(message) > 4096:
                raise OperatorError("Invalid backup request")
            request = json.loads(message)
            if not isinstance(request, dict):
                raise OperatorError("Invalid backup request")
            if request.get("action") == "managed-download":
                stream, size = _managed_open(request.get("run_id"))
                try:
                    self.wfile.write(json.dumps({"ok": True, "size": size}).encode() + b"\n")
                    shutil.copyfileobj(stream, self.wfile, length=1024 * 1024)
                finally:
                    stream.close()
                return
            if request.get("action") == "download":
                stream, size = _open_backup(request.get("name"))
                try:
                    self.wfile.write(json.dumps({"ok": True, "size": size}).encode() + b"\n")
                    shutil.copyfileobj(stream, self.wfile, length=1024 * 1024)
                finally:
                    stream.close()
                return
            result = _dispatch(request)
            self.wfile.write(json.dumps({"ok": True, **result}).encode() + b"\n")
        except (OperatorError, ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
            reason = str(exc) if isinstance(exc, OperatorError) else "Backup operator rejected the request"
            try:
                self.wfile.write(json.dumps({"ok": False, "error": reason}).encode() + b"\n")
            except OSError:
                pass


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def serve():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if os.geteuid() != 0:
        raise SystemExit("Backup operator must run on the host with Docker permissions")
    if SOCKET_PATH.parent.is_symlink():
        raise SystemExit("Unsafe socket directory")
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    # The existing mounted-storage script requires this shared lock directory.
    lock_directory = Path(os.environ.get("LOCK_FILE", "/run/opensigweave/backup.lock")).parent
    if lock_directory.is_symlink():
        raise SystemExit("Unsafe backup lock directory")
    lock_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(lock_directory, 0o700)
    # Docker bind-mounts the socket *file* into the application container.
    # Its host-side parent must remain inaccessible to non-root users.
    if APP_UID <= 0:
        raise SystemExit("BACKUP_APP_UID must be a non-root application UID")
    os.chown(SOCKET_PATH.parent, 0, 0)
    os.chmod(SOCKET_PATH.parent, 0o700)
    if SOCKET_PATH.exists() or SOCKET_PATH.is_symlink():
        if not stat.S_ISSOCK(SOCKET_PATH.lstat().st_mode):
            raise SystemExit("Refusing to replace non-socket path")
        SOCKET_PATH.unlink()
    with Server(str(SOCKET_PATH), Handler) as server:
        os.chown(SOCKET_PATH, APP_UID, 0)
        os.chmod(SOCKET_PATH, 0o600)
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    serve()
