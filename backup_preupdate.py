"""Pre-update snapshots through the existing durable Backup Rules/Locations engine.

This command runs INSIDE the existing application container, not on the host.
No rule means that backups are intentionally not configured. An enabled but
unusable rule is a failed backup, never silently treated as unconfigured.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
import tempfile

from backup_archive import BackupArchiveError
from backup_encryption import BackupEncryptionError
from backup_engine import (BackupEngineError, _identity, _work_root,
                           restore_application_to_new_directory)
from backup_locations import BackupLocationError
from backup_rules import (
    BackupRuleError, dispatch_run, open_managed_archive, reconcile_active, reserve_run,
)
from database import SessionLocal
from models import BackupLocationDB, BackupRuleDB, BackupRunDB


class PreupdateBackupError(Exception):
    """Safe errors intended to be displayed to administrators."""


def select_rule(db, preferred: str = ""):
    """Prefer PREUPDATE_BACKUP_RULE_ID, else sort enabled rules by name and ID.

    Disabled rules are never selected; enabled rules with a broken or disabled
    location are an error and must never silently bypass backup protection.
    """
    enabled = db.query(BackupRuleDB).filter(BackupRuleDB.is_enabled.is_(True)).all()
    if not enabled:
        if preferred:
            raise PreupdateBackupError("Preferred pre-update backup rule is not enabled or does not exist")
        return None
    ordered = sorted(enabled, key=lambda r: (r.name.casefold(), r.id))
    if preferred:
        matches = [r for r in ordered if r.id == preferred]
        if not matches:
            raise PreupdateBackupError("Preferred pre-update backup rule is not enabled or does not exist")
        ordered = matches

    # A broken rule must not prevent a different usable rule from providing
    # protection. An explicitly preferred rule, however, must fail closed.
    problems = []
    for selected in ordered:
        location = db.get(BackupLocationDB, selected.location_id)
        if location is None or not location.is_enabled:
            problems.append("no enabled destination")
            continue
        if selected.content != "full" or location.type not in {"local", "smb"}:
            problems.append("not a complete, restorable archive")
            continue
        if location.type == "smb" and not location.password_ciphertext:
            problems.append("SMB destination lacks credentials")
            continue
        return selected, location

    raise PreupdateBackupError(
        "Enabled backup rules exist but none is usable"
        + (" (preferred rule invalid)" if preferred else "")
        + "; inspect Backup Rules and Backup Locations"
    )


def detect(preferred: str = "") -> dict:
    with SessionLocal() as db:
        result = select_rule(db, preferred)
        if result is None:
            return {"status": "not_configured"}
        rule, location = result
        return {
            "status": "configured",
            "rule_id": rule.id,
            "rule_name": rule.name,
            "location_name": location.name,
            "location_type": location.type,
        }


def _verify_retained(run, location) -> dict:
    """Re-read the stored ciphertext, validate SHA256 and decrypt every manifest file."""
    expected_sha = run.archive_sha256
    if not expected_sha or not run.size_bytes:
        raise PreupdateBackupError("Backup run has no retained archive integrity metadata")
    with tempfile.TemporaryDirectory(prefix=".osw-preupdate-verify-", dir=_work_root()) as name:
        work = Path(name)
        ciphertext = work / "retained.age"
        size, chunks = open_managed_archive(run, location)
        if size != run.size_bytes:
            raise PreupdateBackupError("Stored backup size differs from the recorded size")
        digest = hashlib.sha256()
        with ciphertext.open("xb") as writer:
            os.chmod(ciphertext, 0o600)
            for block in chunks:
                writer.write(block)
                digest.update(block)
            writer.flush()
            os.fsync(writer.fileno())
        if ciphertext.stat().st_size != size or digest.hexdigest() != expected_sha:
            raise PreupdateBackupError("Stored encrypted backup failed SHA256 verification")
        with _identity(work) as identity:
            manifest = restore_application_to_new_directory(
                ciphertext, identity, work / "extracted",
            )
        dump = work / "extracted" / "database" / "opensigweave.dump"
        if not dump.is_file() or dump.stat().st_size == 0:
            raise PreupdateBackupError("Stored backup cannot be restored: PostgreSQL dump missing")
        try:
            checked = subprocess.run(
                ["pg_restore", "--list", str(dump)],
                capture_output=True, check=False, timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise PreupdateBackupError("PostgreSQL restore validation tool failed") from None
        if checked.returncode:
            raise PreupdateBackupError("PostgreSQL backup cannot be read by pg_restore")
        return manifest


def execute(preferred: str = "") -> dict:
    with SessionLocal() as db:
        selected = select_rule(db, preferred)
        if selected is None:
            return {"status": "not_configured"}
        rule, location = selected
        # This durable manual run cannot change the rule's schedule or retention.
        run = reserve_run(db, rule, location, "manual")
        dispatch_run(db, run)
        reconcile_active(db)
        db.refresh(run)
        if run.status != "success" or not run.is_retained or not run.file_key:
            raise PreupdateBackupError(run.error_detail or "Encrypted backup failed")
        _verify_retained(run, location)
        return {
            "status": "verified",
            "rule_id": rule.id, "rule_name": rule.name,
            "location_name": location.name, "location_type": location.type,
            "run_id": run.id, "file_key": run.file_key,
        }
