"""Secure browser bootstrap and adapter setup API integration tests."""
import hashlib
import os
import re
import unittest
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "a-secret-key-longer-than-thirty-two-characters")
os.environ.setdefault("ENGINE_API_KEY", "a-different-engine-key-longer-than-thirty-two-characters")
os.environ["APP_ALLOWED_HOSTS"] = "signature-test.example.org,app.example.org"

from auth_config import hash_local_admin_password
os.environ["LOCAL_ADMIN_EMAIL"] = "admin@example.org"
os.environ["LOCAL_ADMIN_PASSWORD_HASH"] = hash_local_admin_password("correct horse battery staple")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from database import Base
from models import LocalAdminDB, SystemSetupDB


class BrowserBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

        def db_override():
            with self.Session() as session:
                yield session

        self.previous = main.app.dependency_overrides.get(main.get_db)
        main.app.dependency_overrides[main.get_db] = db_override
        self.client = TestClient(main.app, base_url="https://app.example.org",
                                 follow_redirects=False)
        self.token = "local-web-setup-secret-token-" + "x" * 48
        self.patches = [
            patch.object(main, "LOCAL_ADMIN_CONFIGURED", False),
            patch.object(main, "LOCAL_ADMIN_BOOTSTRAP_HASH",
                         hashlib.sha256(self.token.encode()).hexdigest()),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        if self.previous is None:
            main.app.dependency_overrides.pop(main.get_db, None)
        else:
            main.app.dependency_overrides[main.get_db] = self.previous
        self.client.close()
        self.engine.dispose()

    def csrf(self, response):
        match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
        self.assertIsNotNone(match, response.text[:500])
        return match.group(1)

    def test_setup_token_required_and_one_time(self):
        self.assertEqual(self.client.get("/login").headers.get("location"), "/setup")
        start = self.client.get("/setup")
        self.assertEqual(start.status_code, 200)
        fields = {
            "csrf_token": self.csrf(start), "setup_token": "invalid" * 7,
            "email": "newadmin@example.org",
            "password": "another correct horse battery",
            "confirm_password": "another correct horse battery",
        }
        bad = self.client.post("/setup", data=fields)
        self.assertEqual(bad.status_code, 400)
        with self.Session() as db:
            self.assertIsNone(db.get(LocalAdminDB, 1))
        fields["csrf_token"] = self.csrf(bad)
        fields["setup_token"] = self.token
        ok = self.client.post("/setup", data=fields)
        self.assertEqual(ok.status_code, 303, ok.text)
        self.assertIn("section=system", ok.headers.get("location", ""))
        with self.Session() as db:
            admin = db.get(LocalAdminDB, 1)
            self.assertEqual(admin.email, "newadmin@example.org")
            self.assertNotEqual(admin.password_hash, fields["password"])
        self.assertEqual(self.client.get("/setup").headers.get("location"), "/login")

    def test_monitoring_preferences_require_admin(self):
        self.assertEqual(self.client.get("/api/admin/mail-adapter-setup").status_code, 403)

    def test_web_admin_can_configure_adapter_in_system(self):
        start = self.client.get("/setup")
        fields = {
            "csrf_token": self.csrf(start), "setup_token": self.token,
            "email": "newadmin@example.org",
            "password": "another correct horse battery",
            "confirm_password": "another correct horse battery",
        }
        self.assertEqual(self.client.post("/setup", data=fields).status_code, 303)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200, response.text[:500])
        self.assertIn('data-mail-setup-required="true"', response.text)
        match = re.search(r'data-csrf-token="([^"]+)"', response.text)
        self.assertIsNotNone(match)
        csrf = match.group(1)
        payload = {
            "mail_server": "mailcow_rspamd",
            "monitoring_enabled": True, "monitoring_interval": 60,
            "controller_url": "http://10.35.12.29:11334",
        }
        saved = self.client.put("/api/admin/mail-adapter-setup", json=payload,
                                headers={"X-CSRF-Token": csrf})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertTrue(saved.json()["setup_completed"])
        self.assertTrue(saved.json()["instructions"]["supported"])
        with self.Session() as db:
            row = db.get(SystemSetupDB, 1)
            self.assertTrue(row.monitoring_enabled)
            self.assertEqual(row.monitoring_interval, 60)

        payload["controller_url"] = "http://8.8.8.8:11334"
        invalid = self.client.put("/api/admin/mail-adapter-setup", json=payload,
                                  headers={"X-CSRF-Token": csrf})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(self.client.get("/api/admin/mail-adapter-setup").json()
                         ["controller_url"], "http://10.35.12.29:11334")


if __name__ == "__main__":
    unittest.main()
