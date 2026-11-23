"""Backup locations: admin permissions, CSRF, persistence, secrets, local and SMB."""
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

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
from database import Base
from models import BackupLocationDB
from backup_locations import BackupLocationError, decrypt_password, ensure_local_directory, test_location


class BackupLocationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "BACKUP_LOCAL_ROOT": str(Path(self.temp.name) / "backups"),
            "BACKUP_LOCATIONS_KEY": Fernet.generate_key().decode("ascii"),
        })
        self.env.start()
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        def get_test_db():
            with self.Session() as db:
                yield db
        self.previous_override = main.app.dependency_overrides.get(main.get_db)
        main.app.dependency_overrides[main.get_db] = get_test_db
        self.client = TestClient(main.app, base_url="https://app.example.org")

    def tearDown(self):
        self.client.close()
        if self.previous_override is None:
            main.app.dependency_overrides.pop(main.get_db, None)
        else:
            main.app.dependency_overrides[main.get_db] = self.previous_override
        self.engine.dispose()
        self.env.stop()
        self.temp.cleanup()

    def auth(self, groups):
        data = base64.b64encode(json.dumps({"user": {"email": "admin@example.org", "groups": groups},
                                           "csrf_token": "location-csrf"}).encode("utf-8"))
        cookie = TimestampSigner(main.session_secret).sign(data).decode("utf-8")
        self.client.cookies.set(os.getenv("SESSION_COOKIE_NAME", "__Host-opensigweave-session"), cookie)

    def request(self, method, path, payload=None, csrf=True):
        return self.client.request(method, path, json=payload,
                                   headers={"X-CSRF-Token": "location-csrf"} if csrf else {})

    def local(self, name="Office disk"):
        return {"name": name, "type": "local", "enabled": True}

    def smb(self, name="NAS"):
        return {"name": name, "type": "smb", "enabled": True, "server": "nas.example.org",
                "share": "backup", "directory": "osw/daily", "username": "backup",
                "password": "a-test-secret-123", "domain": "WORKGROUP"}

    def test_access_and_csrf(self):
        url = "/api/admin/backups/locations"
        for role in ([], ["signature-users"]):
            self.auth(role)
            self.assertEqual(self.request("GET", url).status_code, 403)
            self.assertEqual(self.request("POST", url, self.local()).status_code, 403)
            self.assertEqual(self.request("PUT", url + "/" + "a" * 32, self.local()).status_code, 403)
            self.assertEqual(self.request("DELETE", url + "/" + "a" * 32).status_code, 403)
            self.assertEqual(self.request("POST", url + "/" + "a" * 32 + "/test").status_code, 403)
        self.auth(["signature-admins"])
        self.assertEqual(self.request("POST", url, self.local(), csrf=False).status_code, 403)
        self.assertEqual(self.request("POST", url + "/" + "a" * 32 + "/test", csrf=False).status_code, 403)

    def test_local_crud_and_independent_persistence(self):
        self.auth(["signature-admins"])
        path = "/api/admin/backups/locations"
        a = self.request("POST", path, self.local())
        self.assertEqual(a.status_code, 201, a.text)
        location = a.json()
        folder = Path(location["local_path"])
        self.assertTrue(folder.is_dir())
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.request("POST", path, self.local("office DISK")).status_code, 409)
        self.assertEqual(len(self.request("GET", path).json()), 1)
        self.assertTrue(self.request("POST", path + "/" + location["id"] + "/test").json()["ok"])
        update = self.request("PUT", path + "/" + location["id"], self.local("Backup A") | {"enabled": False})
        self.assertEqual(update.status_code, 200, update.text)
        self.assertFalse(update.json()["enabled"])
        with self.Session() as db:
            self.assertEqual(db.query(BackupLocationDB).count(), 1)
        self.assertEqual(self.request("DELETE", path + "/" + location["id"]).status_code, 200)
        self.assertEqual(self.request("GET", path).json(), [])
        self.assertTrue(folder.exists(), "Deleting location must not delete existing backup data")

    def test_smb_password_never_returned_and_preserved_on_update(self):
        self.auth(["signature-admins"])
        path = "/api/admin/backups/locations"
        created = self.request("POST", path, self.smb())
        self.assertEqual(created.status_code, 201, created.text)
        row = created.json()
        self.assertTrue(row["has_password"])
        self.assertNotIn("a-test-secret-123", created.text)
        self.assertNotIn("ciphertext", created.text)
        self.assertNotIn("password", row)
        with self.Session() as db:
            saved = db.get(BackupLocationDB, row["id"])
            original = saved.password_ciphertext
            self.assertNotIn("a-test-secret-123", original)
            self.assertEqual(decrypt_password(original), "a-test-secret-123")
        edited = self.smb("NAS renewed")
        edited["password"] = ""
        updated = self.request("PUT", path + "/" + row["id"], edited)
        self.assertEqual(updated.status_code, 200, updated.text)
        with self.Session() as db:
            self.assertEqual(db.get(BackupLocationDB, row["id"]).password_ciphertext, original)
        edited["password"] = "new-test-password"
        self.assertEqual(self.request("PUT", path + "/" + row["id"], edited).status_code, 200)
        with self.Session() as db:
            self.assertEqual(decrypt_password(db.get(BackupLocationDB, row["id"]).password_ciphertext), "new-test-password")
        self.assertNotIn("new-test-password", self.request("GET", path).text)

    def test_invalid_paths_credentials_and_missing_key(self):
        self.auth(["signature-admins"])
        url = "/api/admin/backups/locations"
        for subdir in ("../etc", "/absolute", "a//b", "good/..", r"a\b"):
            payload = self.smb()
            payload["directory"] = subdir
            response = self.request("POST", url, payload)
            self.assertEqual(response.status_code, 422, (subdir, response.text))
            self.assertNotIn(payload["password"], response.text)
        payload = self.smb()
        payload["server"] = "nas.example.org;id"
        self.assertEqual(self.request("POST", url, payload).status_code, 422)
        with patch.dict(os.environ, {"BACKUP_LOCATIONS_KEY": ""}):
            response = self.request("POST", url, self.smb())
            self.assertEqual(response.status_code, 503)
            self.assertIn("BACKUP_LOCATIONS_KEY", response.text)

    def test_local_symlink_attack_fails_closed(self):
        self.auth(["signature-admins"])
        a = self.request("POST", "/api/admin/backups/locations", self.local()).json()
        path = Path(a["local_path"])
        path.rmdir()
        path.symlink_to(Path(self.temp.name), target_is_directory=True)
        response = self.request("POST", "/api/admin/backups/locations/" + a["id"] + "/test")
        self.assertEqual(response.status_code, 503)
        self.assertTrue(path.is_symlink())

    def test_mocked_smb_failures_and_success(self):
        self.auth(["signature-admins"])
        a = self.request("POST", "/api/admin/backups/locations", self.smb()).json()
        url = "/api/admin/backups/locations/" + a["id"] + "/test"
        with patch("smbclient.register_session", side_effect=TimeoutError):
            response = self.request("POST", url)
            self.assertEqual(response.status_code, 502)
            self.assertIn("timed out", response.text)
            self.assertNotIn("a-test-secret-123", response.text)
        with patch("smbclient.register_session", side_effect=PermissionError):
            self.assertIn("authentication", self.request("POST", url).text)
        with patch("smbclient.register_session") as register, \
             patch("smbclient.listdir", return_value=[]), \
             patch("smbclient.open_file") as op, \
             patch("smbclient.remove"):
            op.return_value.__enter__.return_value.write.return_value = 26
            response = self.request("POST", url)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["ok"])
            self.assertTrue(register.call_args.kwargs["encrypt"])
            self.assertTrue(register.call_args.kwargs["require_signing"])


if __name__ == "__main__":
    unittest.main()
