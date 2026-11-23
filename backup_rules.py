"""Durable backup rules, UTC scheduling, in-container snapshots and publication.

No host backup operator, privileged container, Docker socket or host SMB mount
is needed for manual or scheduled rule-managed backups.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import logging
import os
import re
import threading
import time
import uuid

from sqlalchemy import update

from backup_engine import BackupEngineError, create_application_backup
from backup_locations import BackupLocationError, ensure_local_directory, decrypt_password, _smb_unc
from database import SessionLocal
from models import BackupLocationDB, BackupRuleDB, BackupRunDB, BackupRuntimeDB

LOG = logging.getLogger("opensigweave.backup_rules")
RUN_ID = re.compile(r"[0-9a-f]{32}\Z")
TRANSFER_LEASE = timedelta(hours=3)
PENDING_GRACE = timedelta(minutes=3)
SCHEDULES = {"daily", "weekly", "monthly", "interval"}


class BackupRuleError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def utc(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        # SQLite returns timezone-naive UTC values despite timezone=True.
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def next_occurrence(rule, after: datetime) -> datetime:
    """Always advance strictly into the future; UTC avoids DST ambiguities."""
    after = utc(after)
    kind = rule.schedule_kind
    if kind == "interval":
        return after + timedelta(minutes=rule.interval_minutes)
    candidate = after.replace(hour=rule.hour, minute=rule.minute, second=0, microsecond=0)
    if kind == "daily":
        if candidate <= after:
            candidate += timedelta(days=1)
    elif kind == "weekly":
        candidate += timedelta(days=(rule.weekday - after.weekday()) % 7)
        if candidate <= after:
            candidate += timedelta(days=7)
    elif kind == "monthly":
        # Month day is deliberately restricted to 1-28 (never skipped).
        candidate = candidate.replace(day=rule.monthday)
        if candidate <= after:
            year = candidate.year + (candidate.month == 12)
            month = candidate.month % 12 + 1
            candidate = candidate.replace(year=year, month=month)
    else:
        raise BackupRuleError("Unsupported backup schedule")
    return candidate


def apply_rule(row: BackupRuleDB, values: dict, *, now: datetime | None = None) -> None:
    name = values.get("name")
    description = values.get("description") or ""
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120 or any(ord(c) < 32 for c in name):
        raise BackupRuleError("Rule name must contain 1–120 printable characters")
    if not isinstance(description, str) or len(description) > 500 or any(ord(c) < 32 for c in description):
        raise BackupRuleError("Description must contain at most 500 printable characters")
    if values.get("content") != "full":
        # Full is the only safe, tested restore format. Selective component
        # backups would require a different manifest and restore contract.
        raise BackupRuleError("Only complete, restorable encrypted backups are supported")
    kind = values.get("schedule_kind")
    if kind not in SCHEDULES or values.get("timezone") != "UTC":
        raise BackupRuleError("Choose a supported schedule in UTC")
    for key, start, end in (
        ("hour", 0, 23), ("minute", 0, 59), ("weekday", 0, 6),
        ("monthday", 1, 28), ("interval_minutes", 15, 43200),
    ):
        val = values.get(key)
        if type(val) is not int or not start <= val <= end:
            raise BackupRuleError("Invalid backup schedule parameter: " + key)
    count, days = values.get("retention_count"), values.get("retention_days")
    if count is None and days is None:
        raise BackupRuleError("At least one retention limit must be configured")
    if count is not None and (type(count) is not int or not 1 <= count <= 10000):
        raise BackupRuleError("Retention count must be between 1 and 10000")
    if days is not None and (type(days) is not int or not 1 <= days <= 3650):
        raise BackupRuleError("Retention age must be between 1 and 3650 days")
    if type(values.get("enabled")) is not bool:
        raise BackupRuleError("Enabled must be a boolean")
    row.name, row.description = name.strip(), description.strip()
    row.location_id = values["location_id"]
    row.is_enabled, row.content = values["enabled"], "full"
    row.schedule_kind, row.timezone = kind, "UTC"
    row.hour, row.minute = values["hour"], values["minute"]
    row.weekday, row.monthday = values["weekday"], values["monthday"]
    row.interval_minutes = values["interval_minutes"]
    row.retention_count, row.retention_days = count, days
    row.next_run_at = next_occurrence(row, now or utc()) if row.is_enabled else None


def public_rule(row: BackupRuleDB, location: BackupLocationDB | None = None) -> dict:
    return {
        "id": row.id, "name": row.name, "description": row.description,
        "enabled": bool(row.is_enabled), "location_id": row.location_id,
        "location_name": location.name if location else "",
        "content": row.content, "schedule_kind": row.schedule_kind,
        "hour": row.hour, "minute": row.minute, "weekday": row.weekday,
        "monthday": row.monthday, "interval_minutes": row.interval_minutes,
        "timezone": row.timezone, "retention_count": row.retention_count,
        "retention_days": row.retention_days,
        "next_run_at": utc(row.next_run_at).isoformat() if row.next_run_at else None,
    }


def public_run(row: BackupRunDB) -> dict:
    started, finished = utc(row.started_at) if row.started_at else None, utc(row.finished_at) if row.finished_at else None
    return {
        "id": row.id, "rule_id": row.rule_id, "rule_name": row.rule_name,
        "location_id": row.location_id, "location_name": row.location_name,
        "trigger": row.trigger, "status": "running" if row.status == "transferring" else row.status,
        "content": row.content, "requested_at": utc(row.requested_at).isoformat(),
        "started_at": started.isoformat() if started else None,
        "finished_at": finished.isoformat() if finished else None,
        "duration_seconds": round((finished - started).total_seconds(), 2) if started and finished else None,
        "size_bytes": row.size_bytes, "file_key": row.file_key,
        "is_retained": bool(row.is_retained and row.status == "success"), "error_detail": row.error_detail,
    }


def _release(db, run_id: str):
    db.execute(update(BackupRuntimeDB).where(
        BackupRuntimeDB.id == 1, BackupRuntimeDB.active_run_id == run_id
    ).values(active_run_id=None))


def _failed(db, run: BackupRunDB, reason: str):
    run.status = "failed"
    run.is_retained = False
    run.error_detail = reason[:300]  # only internally generated, safe messages
    run.finished_at = utc()
    _release(db, run.id)
    db.commit()


def reserve_run(db, rule: BackupRuleDB, location: BackupLocationDB, trigger: str, now: datetime | None = None) -> BackupRunDB:
    """Atomically claim the database-wide execution slot before contacting host."""
    now = utc(now)
    if not rule.is_enabled or not location.is_enabled:
        raise BackupRuleError("Enable both the backup rule and its location", 409)
    run_id = uuid.uuid4().hex
    result = db.execute(update(BackupRuntimeDB).where(
        BackupRuntimeDB.id == 1, BackupRuntimeDB.active_run_id.is_(None)
    ).values(active_run_id=run_id))
    if result.rowcount != 1:
        db.rollback()
        raise BackupRuleError("Another backup is already pending or running", 409)
    run = BackupRunDB(
        id=run_id, rule_id=rule.id, rule_name=rule.name,
        location_id=location.id, location_name=location.name,
        content="full", trigger=trigger, status="pending",
        requested_at=now,
    )
    db.add(run)
    if trigger == "scheduled":
        rule.next_run_at = next_occurrence(rule, now)
    db.commit()
    return run


def dispatch_run(db, run: BackupRunDB):
    """Queue a durable run for the application scheduler.

    No host socket call is made. The pending state survives a web restart.
    """
    run.status = "pending"
    db.commit()


def _archive_key(run: BackupRunDB) -> str:
    if not RUN_ID.fullmatch(run.id):
        raise BackupRuleError("Invalid backup archive identity")
    rule_id = run.rule_id
    if rule_id is None and isinstance(run.file_key, str):
        # Deleting a rule retains its run history and encrypted archives.
        # Recover the immutable original rule ID only from the stored,
        # identity-bound archive name, never from client input.
        match = re.fullmatch(
            r"opensigweave-([a-f0-9]{32})-" + re.escape(run.id) + r"\.age",
            run.file_key,
        )
        if match:
            rule_id = match.group(1)
    if not isinstance(rule_id, str) or not RUN_ID.fullmatch(rule_id):
        raise BackupRuleError("Invalid backup archive identity")
    return "opensigweave-" + rule_id + "-" + run.id + ".age"


def _transfer_stream(stream, sink, expected_size: int, expected_digest: str):
    digest = hashlib.sha256()
    length = 0
    for block in stream:
        sink.write(block)
        digest.update(block)
        length += len(block)
        if length > expected_size:
            raise BackupRuleError("Encrypted archive size mismatch")
    if length != expected_size or digest.hexdigest() != expected_digest:
        raise BackupRuleError("Encrypted archive integrity check failed")


def _verify_stream(reader, size: int, digest: str):
    actual = hashlib.sha256()
    count = 0
    for block in iter(lambda: reader.read(1024 * 1024), b""):
        actual.update(block)
        count += len(block)
        if count > size:
            break
    return count == size and actual.hexdigest() == digest


def _local_publish(location, key, blocks, size, sha):
    directory = ensure_local_directory(location.id)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = "." + key + "." + uuid.uuid4().hex + ".tmp"
    try:
        try:
            existing = os.open(key, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            with os.fdopen(existing, "rb") as reader:
                if not _verify_stream(reader, size, sha):
                    raise BackupRuleError("Existing encrypted archive differs")
            return
        file_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(file_fd, "wb") as writer:
                _transfer_stream(blocks, writer, size, sha)
                writer.flush()
                os.fsync(writer.fileno())
            os.rename(temporary, key, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass
    finally:
        os.close(fd)


def _smb_session(location):
    import smbclient
    password = decrypt_password(location.password_ciphertext)
    username = ((location.domain + "\\") if location.domain else "") + location.username
    smbclient.register_session(
        location.server, username=username, password=password,
        encrypt=True, require_signing=True, connection_timeout=10,
    )
    return smbclient


def _smb_publish(location, key, blocks, size, sha):
    try:
        client = _smb_session(location)
        root = _smb_unc(location)
        final = root + "\\" + key
        temporary = root + "\\." + key + "." + uuid.uuid4().hex + ".tmp"
        if client.path.exists(final):
            with client.open_file(final, mode="rb") as reader:
                if not _verify_stream(reader, size, sha):
                    raise BackupRuleError("Existing encrypted archive differs")
            return
        try:
            with client.open_file(temporary, mode="xb") as writer:
                _transfer_stream(blocks, writer, size, sha)
            with client.open_file(temporary, mode="rb") as reader:
                if not _verify_stream(reader, size, sha):
                    raise BackupRuleError("Remote encrypted archive integrity check failed")
            client.rename(temporary, final)
        finally:
            try:
                client.remove(temporary)
            except Exception:
                pass
    except BackupRuleError:
        raise
    except Exception:
        # SMB exceptions often include server paths, usernames or secrets.
        raise BackupRuleError("SMB destination is unavailable or refused the encrypted backup") from None


def publish_run(run: BackupRunDB, location: BackupLocationDB):
    """Build and cryptographically verify ciphertext before atomic publication."""
    key = _archive_key(run)

    def deliver(encrypted, size, digest):
        def stream():
            with encrypted.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    yield chunk

        if location.type == "local":
            _local_publish(location, key, stream(), size, digest)
        elif location.type == "smb":
            _smb_publish(location, key, stream(), size, digest)
        else:
            raise BackupRuleError("Unsupported backup destination type")

    size, digest = create_application_backup(deliver)
    return key, size, digest



def open_managed_archive(run: BackupRunDB, location: BackupLocationDB):
    """Authorized download of a previously verified, retained ciphertext file."""
    if (run.status != "success" or not run.is_retained or
            run.location_id != location.id or run.file_key != _archive_key(run)):
        raise BackupRuleError("Encrypted backup is not available", 404)
    key = run.file_key
    if location.type == "local":
        directory = ensure_local_directory(location.id)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fd = os.open(key, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        finally:
            os.close(directory_fd)
        import stat
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size != run.size_bytes:
            os.close(fd)
            raise BackupRuleError("Encrypted archive is missing or changed", 404)

        def stream():
            with os.fdopen(fd, "rb") as reader:
                for block in iter(lambda: reader.read(1024 * 1024), b""):
                    yield block
        return info.st_size, stream()
    if location.type == "smb":
        try:
            client = _smb_session(location)
            file = client.open_file(_smb_unc(location) + "\\" + key, mode="rb")
            size = file.seek(0, 2)
            file.seek(0)
            if size != run.size_bytes:
                file.close()
                raise BackupRuleError("Encrypted archive is missing or changed", 404)

            def stream():
                with file:
                    for block in iter(lambda: file.read(1024 * 1024), b""):
                        yield block
            return size, stream()
        except BackupRuleError:
            raise
        except Exception:
            raise BackupRuleError("Encrypted SMB archive cannot be opened", 503) from None
    raise BackupRuleError("Unsupported archive location", 422)

def _delete_archive(location: BackupLocationDB, key: str):
    if not re.fullmatch(r"opensigweave-[a-f0-9]{32}-[a-f0-9]{32}\.age", key):
        raise BackupRuleError("Invalid managed archive name")
    if location.type == "local":
        directory = ensure_local_directory(location.id)
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            # Only unlink non-symlink regular files.
            info = os.stat(key, dir_fd=fd, follow_symlinks=False)
            import stat
            if not stat.S_ISREG(info.st_mode):
                raise BackupRuleError("Unsafe local archive; retention stopped")
            os.unlink(key, dir_fd=fd)
        finally:
            os.close(fd)
    elif location.type == "smb":
        try:
            client = _smb_session(location)
            client.remove(_smb_unc(location) + "\\" + key)
        except Exception:
            raise BackupRuleError("SMB retention could not remove an archived backup") from None


def prune_rule(db, rule: BackupRuleDB, now: datetime | None = None):
    """Delete only successful, DB-owned archives of THIS rule/location.

    Always retain the newest successful backup. History entries are preserved.
    """
    now = utc(now)
    location = db.get(BackupLocationDB, rule.location_id)
    if location is None:
        return
    items = db.query(BackupRunDB).filter(
        BackupRunDB.rule_id == rule.id, BackupRunDB.location_id == location.id,
        BackupRunDB.status == "success", BackupRunDB.is_retained.is_(True),
        BackupRunDB.file_key.is_not(None),
    ).order_by(BackupRunDB.finished_at.desc()).all()
    for position, item in enumerate(items):
        if position == 0:
            continue
        by_count = rule.retention_count is not None and position >= rule.retention_count
        by_age = rule.retention_days is not None and utc(item.finished_at) < now - timedelta(days=rule.retention_days)
        if by_count or by_age:
            # Identity-derived archive key prevents arbitrary deletes and
            # ensures rule/file membership even if the DB is manipulated.
            if item.file_key != _archive_key(item):
                raise BackupRuleError("Archive ownership verification failed")
            _delete_archive(location, item.file_key)
            item.is_retained = False
            db.commit()


def reconcile_active(db, now: datetime | None = None):
    """Execute one durable pending run and fail closed after interrupted work."""
    now = utc(now)
    lease = db.get(BackupRuntimeDB, 1)
    if lease is None or not lease.active_run_id:
        return
    run = db.get(BackupRunDB, lease.active_run_id)
    if run is None or run.status in {"success", "failed"}:
        _release(db, lease.active_run_id)
        db.commit()
        return
    if run.status == "transferring":
        if run.transfer_started_at and utc(run.transfer_started_at) + TRANSFER_LEASE < now:
            _failed(db, run, "Interrupted container backup; verify recovery before starting another")
        return
    if run.status != "pending":
        _failed(db, run, "Unsupported or interrupted backup execution state")
        return
    claimed = db.execute(update(BackupRunDB).where(
        BackupRunDB.id == run.id, BackupRunDB.status == "pending"
    ).values(status="transferring", started_at=now, transfer_started_at=now))
    if claimed.rowcount != 1:
        db.rollback()
        return
    db.commit()
    location = db.get(BackupLocationDB, run.location_id)
    if location is None:
        _failed(db, run, "Backup location was removed")
        return
    try:
        key, size, digest = publish_run(run, location)
        run.file_key, run.size_bytes, run.archive_sha256 = key, size, digest
        run.status, run.finished_at, run.error_detail = "success", utc(), None
        _release(db, run.id)
        db.commit()
        rule = db.get(BackupRuleDB, run.rule_id) if run.rule_id else None
        if rule:
            try:
                prune_rule(db, rule)
            except (OSError, BackupRuleError, BackupLocationError):
                LOG.warning("Retention failed for backup rule %s", rule.id)
    except (BackupEngineError, BackupRuleError, BackupLocationError, OSError):
        db.rollback()
        _failed(db, run, "Container backup, encryption or destination verification failed")
    except Exception:
        # Never log raw library exceptions: SMB and crypto errors may contain secrets.
        db.rollback()
        _failed(db, run, "Container backup failed unexpectedly")



def run_scheduler_tick(now: datetime | None = None):
    now = utc(now)
    with SessionLocal() as db:
        reconcile_active(db, now)
        lease = db.get(BackupRuntimeDB, 1)
        if lease is None or lease.active_run_id:
            return
        due = db.query(BackupRuleDB).join(
            BackupLocationDB, BackupRuleDB.location_id == BackupLocationDB.id
        ).filter(
            BackupLocationDB.is_enabled.is_(True), BackupRuleDB.is_enabled.is_(True),
            BackupRuleDB.next_run_at.is_not(None),
            BackupRuleDB.next_run_at <= now,
        ).order_by(BackupRuleDB.next_run_at, BackupRuleDB.id).limit(1).first()
        if due is None:
            return
        location = db.get(BackupLocationDB, due.location_id)
        if location is None or not location.is_enabled:
            # Retry later instead of consuming a due run and creating noise.
            return
        try:
            run = reserve_run(db, due, location, "scheduled", now)
        except BackupRuleError:
            return
        dispatch_run(db, run)


_worker_stop = threading.Event()
_worker_thread: threading.Thread | None = None


def start_scheduler():
    global _worker_thread
    if os.environ.get("BACKUP_RULE_SCHEDULER_ENABLED", "true").lower() != "true":
        return
    if _worker_thread is not None and _worker_thread.is_alive():
        return
    _worker_stop.clear()

    def loop():
        while not _worker_stop.is_set():
            try:
                run_scheduler_tick()
            except Exception:
                LOG.exception("Backup rule scheduler tick failed")
            _worker_stop.wait(20)

    _worker_thread = threading.Thread(target=loop, daemon=True, name="opensigweave-backup-scheduler")
    _worker_thread.start()


def stop_scheduler():
    _worker_stop.set()
