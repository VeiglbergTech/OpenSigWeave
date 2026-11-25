"""Backup Rules: UTC scheduling, persistence, concurrency, history and retention.

No Docker daemon, real SMB share or scheduled cron service is required.
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import io
from types import SimpleNamespace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECRET_KEY", "test-secret-key-with-more-than-thirty-two-characters")
os.environ.setdefault("ENGINE_API_KEY", "test-engine-api-key-with-more-than-thirty-two-characters")
os.environ["APP_ALLOWED_HOSTS"] = "signature-test.example.org,app.example.org"
from auth_config import hash_local_admin_password
os.environ["LOCAL_ADMIN_EMAIL"] = "admin@example.org"
os.environ["LOCAL_ADMIN_PASSWORD_HASH"] = hash_local_admin_password("correct horse battery staple")

import main
import backup_rules
from backup_engine import BackupEngineError
from database import Base
from models import BackupLocationDB, BackupRuleDB, BackupRunDB, BackupRuntimeDB


class BackupRuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {
            "BACKUP_LOCAL_ROOT": str(Path(self.tmp.name) / "archives"),
            "BACKUP_LOCATIONS_KEY": Fernet.generate_key().decode(),
            "BACKUP_RULE_SCHEDULER_ENABLED": "false",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.Session.begin() as db:
            db.add(BackupRuntimeDB(id=1))
        def get_db():
            with self.Session() as db:
                yield db
        self.previous = main.app.dependency_overrides.get(main.get_db)
        main.app.dependency_overrides[main.get_db] = get_db
        self.addCleanup(self.reset_db)
        self.client = TestClient(main.app, base_url="https://app.example.org")
        self.addCleanup(self.client.close)

    def reset_db(self):
        if self.previous is None:
            main.app.dependency_overrides.pop(main.get_db, None)
        else:
            main.app.dependency_overrides[main.get_db] = self.previous
        self.engine.dispose()

    def auth(self, roles):
        payload = base64.b64encode(json.dumps({
            "user": {"email": "admin@example.org", "groups": roles}, "csrf_token": "backup-rule-csrf"
        }).encode())
        cookie = TimestampSigner(main.session_secret).sign(payload).decode()
        self.client.cookies.set(os.getenv("SESSION_COOKIE_NAME", "__Host-opensigweave-session"), cookie)

    def send(self, method, url, body=None, csrf=True):
        return self.client.request(
            method, url, json=body,
            headers={"X-CSRF-Token": "backup-rule-csrf"} if csrf else {},
        )

    def location(self, name="Location A"):
        return self.send("POST", "/api/admin/backups/locations",
                         {"name": name, "type": "local", "enabled": True}).json()

    def rule(self, location_id, name="Nightly", **changes):
        return {
            "name": name, "location_id": location_id, "enabled": True,
            "content": "full", "schedule_kind": "daily", "timezone": "UTC",
            "hour": 2, "minute": 0, "weekday": 0, "monthday": 1,
            "interval_minutes": 1440, "retention_count": 2, "retention_days": 30,
            **changes,
        }

    def create_rule(self, location_id, **changes):
        result = self.send("POST", "/api/admin/backups/rules", self.rule(location_id, **changes))
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()

    def test_authentication_csrf_crud_and_history(self):
        url = "/api/admin/backups/rules"
        self.auth(["signature-users"])
        self.assertEqual(self.send("GET", url).status_code, 403)
        self.assertEqual(self.send("POST", url, self.rule("a" * 32)).status_code, 403)
        self.assertEqual(self.send("GET", "/api/admin/backups/history").status_code, 403)
        self.auth(["signature-admins"])
        loc = self.location()
        self.assertEqual(self.send("POST", url, self.rule(loc["id"]), csrf=False).status_code, 403)
        rule = self.create_rule(loc["id"])
        self.assertEqual(len(self.send("GET", url).json()), 1)
        self.assertEqual(rule["timezone"], "UTC")
        self.assertTrue(rule["next_run_at"])
        self.assertEqual(self.send("POST", url, self.rule(loc["id"])).status_code, 409)
        updated = self.send("PUT", url + "/" + rule["id"], self.rule(
            loc["id"], name="Weekly", schedule_kind="weekly", weekday=4, enabled=False
        ))
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertIsNone(updated.json()["next_run_at"])
        self.assertFalse(updated.json()["enabled"])
        self.assertEqual(self.send("DELETE", url + "/" + rule["id"]).status_code, 200)
        self.assertEqual(self.send("GET", url).json(), [])

    def test_input_validation_and_retention_configuration(self):
        self.auth(["signature-admins"])
        loc = self.location()
        for change in (
            {"content": "database"}, {"timezone": "Europe/Berlin"},
            {"retention_count": None, "retention_days": None},
            {"interval_minutes": 1}, {"monthday": 31},
        ):
            with self.subTest(change=change):
                r = self.send("POST", "/api/admin/backups/rules", self.rule(loc["id"], **change))
                self.assertEqual(r.status_code, 422, r.text)

    def test_rule_settings_are_independent_and_legacy_globals_rejected(self):
        self.auth(["signature-admins"])
        primary = self.location("Local Primary")
        secondary = self.location("Local Secondary")
        first = self.create_rule(
            primary["id"], name="Daily", schedule_kind="daily",
            hour=3, minute=15, retention_count=2, retention_days=None,
            description="Primary location",
        )
        second = self.create_rule(
            secondary["id"], name="Interval", schedule_kind="interval",
            interval_minutes=45, retention_count=8, retention_days=60,
            description="Secondary location",
        )
        self.assertNotEqual(first["next_run_at"], second["next_run_at"])
        self.assertEqual(first["retention_count"], 2)
        self.assertEqual(second["retention_count"], 8)
        self.assertEqual(second["retention_days"], 60)
        self.assertEqual(second["location_id"], secondary["id"])
        self.assertEqual(second["interval_minutes"], 45)
        response = self.send("PUT", "/api/admin/backups/rules/" + first["id"], self.rule(
            primary["id"], name="Daily", enabled=False,
            hour=3, minute=15, retention_count=2, retention_days=None,
        ))
        self.assertEqual(response.status_code, 200, response.text)
        rules = {row["id"]: row for row in self.send("GET", "/api/admin/backups/rules").json()}
        self.assertFalse(rules[first["id"]]["enabled"])
        self.assertTrue(rules[second["id"]]["enabled"])
        self.assertEqual(rules[second["id"]]["retention_count"], 8)
        self.assertEqual(self.send("POST", "/api/admin/backups/rules/" + first["id"] + "/run").status_code, 409)
        manual = self.send("POST", "/api/admin/backups/rules/" + second["id"] + "/run")
        self.assertEqual(manual.status_code, 202, manual.text)
        self.assertEqual(manual.json()["location_id"], secondary["id"])
        self.assertEqual(manual.json()["content"], "full")
        self.assertEqual(manual.json()["trigger"], "manual")
        # Deprecated global options must not silently mutate individual rules.
        for field, value in (("directory", "/mnt/backups"), ("automatic", True), ("keep", 1)):
            with self.subTest(field=field):
                data = self.rule(primary["id"], name="Legacy", **{field: value})
                result = self.send("POST", "/api/admin/backups/rules", data)
                self.assertEqual(result.status_code, 422, result.text)

    def test_manual_queue_executes_when_automatic_dispatch_is_paused(self):
        self.auth(["signature-admins"])
        location = self.location()
        manual_rule = self.create_rule(location["id"], name="Manual")
        scheduled_rule = self.create_rule(location["id"], name="Scheduled")
        now = datetime.now(timezone.utc)
        with self.Session() as db:
            due = db.get(BackupRuleDB, scheduled_rule["id"])
            due.next_run_at = now - timedelta(minutes=1)
            db.commit()
        response = self.send("POST", "/api/admin/backups/rules/" + manual_rule["id"] + "/run")
        self.assertEqual(response.status_code, 202, response.text)
        data = b"encrypted manual ciphertext"
        digest = hashlib.sha256(data).hexdigest()

        def deliver_local(deliver):
            archive = Path(self.tmp.name) / "manual.age"
            archive.write_bytes(data)
            deliver(archive, len(data), digest)
            return len(data), digest

        with patch.object(backup_rules, "SessionLocal", self.Session), \
             patch.object(backup_rules, "create_application_backup", side_effect=deliver_local):
            backup_rules.run_scheduler_tick(now, allow_scheduled=False)
            backup_rules.run_scheduler_tick(now, allow_scheduled=False)
        with self.Session() as db:
            runs = db.query(BackupRunDB).all()
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0].trigger, "manual")
            self.assertEqual(runs[0].status, "success")
            self.assertLess(backup_rules.utc(db.get(BackupRuleDB, scheduled_rule["id"]).next_run_at), now)

    def test_utc_schedule_boundaries(self):
        instant = datetime(2026, 11, 23, 2, 0, tzinfo=timezone.utc)  # Monday
        class Rule: pass
        item = Rule()
        item.hour, item.minute = 2, 0
        item.weekday, item.monthday, item.interval_minutes = 0, 28, 15
        item.schedule_kind = "daily"
        self.assertEqual(backup_rules.next_occurrence(item, instant), instant + timedelta(days=1))
        item.schedule_kind = "weekly"
        self.assertEqual(backup_rules.next_occurrence(item, instant), instant + timedelta(days=7))
        item.schedule_kind = "monthly"
        self.assertEqual(backup_rules.next_occurrence(item, instant), instant.replace(day=28))
        self.assertEqual(backup_rules.next_occurrence(item, instant.replace(day=28)),
                         instant.replace(month=12, day=28))
        item.schedule_kind = "interval"
        self.assertEqual(backup_rules.next_occurrence(item, instant), instant + timedelta(minutes=15))

    def test_manual_run_container_failure_and_history(self):
        self.auth(["signature-admins"])
        location = self.location()
        rule = self.create_rule(location["id"])
        response = self.send("POST", "/api/admin/backups/rules/" + rule["id"] + "/run")
        self.assertEqual(response.status_code, 202, response.text)
        record = response.json()
        self.assertEqual(record["status"], "pending")
        with patch.object(backup_rules, "SessionLocal", self.Session), \
             patch.object(backup_rules, "create_application_backup",
                          side_effect=BackupEngineError("private secret")):
            backup_rules.run_scheduler_tick()
        history = self.send("GET", "/api/admin/backups/history?status=failed").json()
        self.assertEqual(len(history), 1)
        self.assertNotIn("private secret", json.dumps(history))
        self.assertEqual(self.send("GET", "/api/admin/backups/history/" + record["id"]).json()["status"], "failed")
        with self.Session() as db:
            self.assertIsNone(db.get(BackupRuntimeDB, 1).active_run_id)

    def test_concurrent_reservation_and_multiple_locations(self):
        self.auth(["signature-admins"])
        a, b = self.location("Disk 1"), self.location("Disk 2")
        first, second = self.create_rule(a["id"]), self.create_rule(b["id"], name="Second")
        with self.Session() as db:
            r1, r2 = db.get(BackupRuleDB, first["id"]), db.get(BackupRuleDB, second["id"])
            one, two = db.get(BackupLocationDB, a["id"]), db.get(BackupLocationDB, b["id"])
            run = backup_rules.reserve_run(db, r1, one, "manual")
            with self.assertRaises(backup_rules.BackupRuleError):
                backup_rules.reserve_run(db, r2, two, "manual")
            self.assertEqual(run.location_id, a["id"])
            backup_rules._failed(db, run, "Test failure")
            again = backup_rules.reserve_run(db, r2, two, "manual")
            self.assertEqual(again.location_id, b["id"])

    def test_successful_transfer_then_retention_preserves_foreign_files(self):
        self.auth(["signature-admins"])
        location = self.location()
        rule = self.create_rule(location["id"])
        payload = b"fake encrypted ciphertext for deterministic unit test"
        digest = hashlib.sha256(payload).hexdigest()
        with self.Session() as db:
            db_rule = db.get(BackupRuleDB, rule["id"])
            dest = db.get(BackupLocationDB, location["id"])
            for i in range(3):
                record = BackupRunDB(
                    id=("%032x" % (i + 1)), rule_id=db_rule.id, rule_name=db_rule.name,
                    location_id=dest.id, location_name=dest.name, trigger="manual",
                    status="success", content="full",
                    requested_at=datetime(2026, 11, 20, tzinfo=timezone.utc),
                    started_at=datetime(2026, 11, 20, tzinfo=timezone.utc),
                    finished_at=datetime(2026, 11, 20 + i, tzinfo=timezone.utc),
                    size_bytes=len(payload), archive_sha256=digest,
                )
                record.file_key = backup_rules._archive_key(record)
                db.add(record)
            db.commit()
            directory = backup_rules.ensure_local_directory(dest.id)
            (directory / "do-not-delete.age").write_bytes(b"foreign")
            for item in db.query(BackupRunDB).all():
                (directory / item.file_key).write_bytes(payload)
            backup_rules.prune_rule(db, db_rule, datetime(2026, 11, 23, tzinfo=timezone.utc))
            remaining = [item for item in db.query(BackupRunDB).all() if item.is_retained]
            self.assertEqual(len(remaining), 2)
            self.assertTrue((directory / "do-not-delete.age").exists())
            self.assertEqual(len(list(directory.glob("opensigweave-*.age"))), 2)
            self.assertEqual(self.send("DELETE", "/api/admin/backups/locations/" + location["id"]).status_code, 409)

            # A verified download has no plaintext or arbitrary path exposure.
            size, stream = backup_rules.open_managed_archive(remaining[0], dest)
            self.assertEqual(b"".join(stream), payload)
            self.assertEqual(size, len(payload))

    def test_full_execution_cycle_and_verified_local_publication(self):
        self.auth(["signature-admins"])
        location = self.location()
        rule = self.create_rule(location["id"])
        data = b"age encrypted fake backup for deterministic destination tests"
        digest = hashlib.sha256(data).hexdigest()
        with self.Session() as db:
            db_rule = db.get(BackupRuleDB, rule["id"])
            loc = db.get(BackupLocationDB, location["id"])
            run = backup_rules.reserve_run(db, db_rule, loc, "manual")

            def encrypt_and_deliver(deliver):
                filename = Path(self.tmp.name) / "generated.age"
                filename.write_bytes(data)
                deliver(filename, len(data), digest)
                return len(data), digest

            with patch.object(backup_rules, "create_application_backup", side_effect=encrypt_and_deliver):
                backup_rules.reconcile_active(db)
            db.refresh(run)
            self.assertEqual(run.status, "success")
            self.assertEqual(run.archive_sha256, digest)
            self.assertIsNone(db.get(BackupRuntimeDB, 1).active_run_id)
            self.assertEqual(Path(backup_rules.ensure_local_directory(loc.id) / run.file_key).read_bytes(), data)

    def test_smb_encrypted_publish_and_partial_transfer_cleanup(self):
        """Direct SMB copy with read-back, atomic rename, no host mount."""
        files = {}
        class Writer(io.BytesIO):
            def __init__(self, path):
                super().__init__()
                self.path = path
            def close(self):
                if not self.closed:
                    files[self.path] = self.getvalue()
                super().close()
        class Client:
            path = SimpleNamespace(exists=lambda path: path in files)
            def open_file(self, path, mode):
                if mode == "xb":
                    if path in files:
                        raise FileExistsError(path)
                    return Writer(path)
                if mode == "rb":
                    return io.BytesIO(files[path])
                raise AssertionError(mode)
            def rename(self, src, dest):
                files[dest] = files.pop(src)
            def remove(self, path):
                files.pop(path, None)

        loc = SimpleNamespace(server="nas.example.org", share="archives", directory="osw",
                              username="backup", domain="LOCAL", password_ciphertext="secret")
        data = b"encrypted ciphertext test payload"
        digest = hashlib.sha256(data).hexdigest()
        def blocks():
            yield data[:7]
            yield data[7:]
        with patch.object(backup_rules, "_smb_session", return_value=Client()):
            backup_rules._smb_publish(loc, "archive.age", blocks(), len(data), digest)
            self.assertEqual(len(files), 1)
            self.assertEqual(next(iter(files.values())), data)
            # Repeated upload must verify, not replace, existing archives.
            backup_rules._smb_publish(loc, "archive.age", blocks(), len(data), digest)
            self.assertEqual(len(files), 1)
            with self.assertRaises(backup_rules.BackupRuleError):
                backup_rules._smb_publish(loc, "different.age", iter([data[:5]]), len(data), digest)
            self.assertEqual(len(files), 1, "partial SMB staging files must be removed")
            self.assertNotIn("secret", str(files))

    def test_scheduler_claims_due_and_does_not_duplicate(self):
        self.auth(["signature-admins"])
        location = self.location()
        rule = self.create_rule(location["id"])
        now = datetime.now(timezone.utc)
        with self.Session() as db:
            item = db.get(BackupRuleDB, rule["id"])
            item.next_run_at = now - timedelta(minutes=1)
            db.commit()
        ciphertext = b"encrypted backup"
        digest = hashlib.sha256(ciphertext).hexdigest()

        def deliver_local(deliver):
            path = Path(self.tmp.name) / "scheduled.age"
            path.write_bytes(ciphertext)
            deliver(path, len(ciphertext), digest)
            return len(ciphertext), digest

        with patch.object(backup_rules, "SessionLocal", self.Session), \
             patch.object(backup_rules, "create_application_backup", side_effect=deliver_local):
            backup_rules.run_scheduler_tick(now)
            with self.Session() as db:
                self.assertEqual(db.query(BackupRunDB).one().status, "pending")
            backup_rules.run_scheduler_tick(now)
            backup_rules.run_scheduler_tick(now)
        with self.Session() as db:
            self.assertEqual(db.query(BackupRunDB).count(), 1)
            record = db.query(BackupRunDB).one()
            self.assertEqual(record.trigger, "scheduled")
            self.assertEqual(record.status, "success")


if __name__ == "__main__":
    unittest.main()
