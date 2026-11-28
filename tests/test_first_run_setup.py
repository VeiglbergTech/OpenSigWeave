"""Secure browser bootstrap and adapter setup API integration tests."""
import hashlib
import os
import re
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

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
        self.assertEqual(ok.headers.get("location"), "/admin/system")
        with self.Session() as db:
            admin = db.get(LocalAdminDB, 1)
            self.assertEqual(admin.email, "newadmin@example.org")
            self.assertNotEqual(admin.password_hash, fields["password"])
        self.assertEqual(self.client.get("/setup").headers.get("location"), "/login")

    def test_legacy_admin_keeps_personal_editor_on_first_login(self):
        # A legacy deployment with no mail-server preferences must not
        # unexpectedly hide its personal signature editor on login.
        with patch.object(main, "LOCAL_ADMIN_CONFIGURED", True):
            login = self.client.get("/login")
            self.assertEqual(login.status_code, 200)
            result = self.client.post("/login", data={
                "csrf_token": self.csrf(login),
                "email": "admin@example.org",
                "password": "correct horse battery staple",
            })
            self.assertEqual(result.status_code, 303)
            redirect = self.client.get("/")
            self.assertEqual(redirect.status_code, 303)
            self.assertEqual(redirect.headers["location"], "/user")
            response = self.client.get("/user")
            self.assertEqual(response.status_code, 200)
            self.assertIn('data-mail-setup-required="false"', response.text)

    def test_public_liveness_and_dependency_readiness(self):
        self.assertEqual(self.client.get("/livez").json()["status"], "alive")
        with tempfile.TemporaryDirectory() as directory:
            signatures = Path(directory) / "signatures"
            assets = Path(directory) / "assets"
            signatures.mkdir()
            assets.mkdir()
            store = SimpleNamespace(signatures_root=signatures, assets_root=assets)
            with patch.object(main, "SessionLocal", self.Session), patch.object(
                    main, "signature_file_store", store):
                ready = self.client.get("/readyz")
                self.assertEqual(ready.status_code, 200, ready.text)
                self.assertEqual(ready.json()["status"], "ready")
                assets.rmdir()
                missing = self.client.get("/readyz")
                self.assertEqual(missing.status_code, 503)
                self.assertEqual(missing.json()["status"], "not_ready")

    def test_manual_adapter_check_protected_and_truthful(self):
        self.assertEqual(self.client.post("/api/admin/mail-adapter-check").status_code, 403)
        with patch.object(main, "LOCAL_ADMIN_CONFIGURED", True):
            response = self.client.get("/login")
            token = self.csrf(response)
            result = self.client.post("/login", data={
                "csrf_token": token, "email": "admin@example.org",
                "password": "correct horse battery staple",
            })
            self.assertEqual(result.status_code, 303)
            csrf = re.search(r'data-csrf-token="([^"]+)"', self.client.get("/admin/system").text).group(1)
            saved = self.client.put("/api/admin/mail-adapter-setup", json={
                "mail_server": "rspamd", "monitoring_enabled": False,
                "monitoring_interval": 60, "controller_url": "http://10.35.12.29:11334"
            }, headers={"X-CSRF-Token": csrf})
            self.assertEqual(saved.status_code, 200, saved.text)
            with patch.object(main, "SessionLocal", self.Session), patch(
                    "core.monitor_scheduler.probe", return_value={
                        "status": "configured", "detail": "Both OpenSigWeave symbols are registered"
                    }) as checked:
                result = self.client.post("/api/admin/mail-adapter-check",
                                          headers={"X-CSRF-Token": csrf})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["last_monitor_status"], "configured")
            self.assertIsNotNone(result.json()["last_monitored_at"])
            checked.assert_called_once()


    def _login_and_configure_for_activity(self):
        start = self.client.get("/setup")
        fields = {
            "csrf_token": self.csrf(start), "setup_token": self.token,
            "email": "newadmin@example.org",
            "password": "another correct horse battery",
            "confirm_password": "another correct horse battery",
        }
        self.assertEqual(self.client.post("/setup", data=fields).status_code, 303)
        response = self.client.get("/admin/system")
        csrf = re.search(r'data-csrf-token="([^"]+)"', response.text).group(1)
        payload = {
            "mail_server": "rspamd", "monitoring_enabled": False,
            "monitoring_interval": 300, "controller_url": "",
            "engine_url": "https://app.example.org/api/v1/engine/signatures/",
        }
        saved = self.client.put("/api/admin/mail-adapter-setup",
                                json=payload, headers={"X-CSRF-Token": csrf})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertFalse(saved.json()["setup_completed"])
        return csrf, payload

    def test_only_successful_authenticated_engine_call_verifies_setup(self):
        from core.mail_activity import reset_activity_cache
        reset_activity_cache()
        csrf, payload = self._login_and_configure_for_activity()
        with patch.object(main, "SessionLocal", self.Session), patch.object(
                main, "resolve_signature_engine", new_callable=AsyncMock) as resolver:
            resolver.return_value = SimpleNamespace(
                to_payload=lambda: {"html": "", "inject_on_replies": False})
            endpoint = "/api/v1/engine/signatures/sender@example.org"
            bad = self.client.get(endpoint, headers={"X-Engine-Key": "wrong"})
            self.assertEqual(bad.status_code, 401)
            self.assertEqual(self.client.get("/api/admin/mail-adapter-setup").json()
                             ["connection_status"], "Unverified")
            good = self.client.get(endpoint, headers={"X-Engine-Key": main.ENGINE_API_KEY})
            self.assertEqual(good.status_code, 200, good.text)
        verified = self.client.get("/api/admin/mail-adapter-setup").json()
        self.assertTrue(verified["setup_completed"])
        self.assertEqual(verified["connection_status"], "Active")
        self.assertIsNotNone(verified["last_authenticated_at"])
        with self.Session() as reopened:
            self.assertTrue(reopened.get(SystemSetupDB, 1).setup_completed)
        self.assertEqual(self.client.get("/admin/system").status_code, 200)

        payload["monitoring_enabled"] = True
        payload["controller_url"] = "http://10.35.12.29:11334"
        updated = self.client.put("/api/admin/mail-adapter-setup", json=payload,
                                  headers={"X-CSRF-Token": csrf})
        self.assertTrue(updated.json()["setup_completed"])
        payload["engine_url"] = "https://other.example.org/api/v1/engine/signatures/"
        updated = self.client.put("/api/admin/mail-adapter-setup", json=payload,
                                  headers={"X-CSRF-Token": csrf})
        self.assertFalse(updated.json()["setup_completed"])
        self.assertEqual(updated.json()["connection_status"], "Unverified")
        self.assertEqual(updated.json()["last_authenticated_at"], None)

    def test_remote_https_endpoint_validation_and_advanced_status(self):
        csrf, payload = self._login_and_configure_for_activity()
        payload["engine_url"] = "http://remote.example.org/api/v1/engine/signatures/"
        invalid = self.client.put("/api/admin/mail-adapter-setup", json=payload,
                                  headers={"X-CSRF-Token": csrf})
        self.assertEqual(invalid.status_code, 422)
        payload["engine_url"] = "https://remote.example.org"
        valid = self.client.put("/api/admin/mail-adapter-setup", json=payload,
                                headers={"X-CSRF-Token": csrf})
        self.assertEqual(valid.status_code, 200, valid.text)
        self.assertEqual(valid.json()["engine_url"],
                         "https://remote.example.org/api/v1/engine/signatures/")
        from datetime import datetime, timezone
        from main import _public_system_settings
        with self.Session() as db:
            row = db.get(SystemSetupDB, 1)
            row.monitoring_enabled = True
            row.last_monitor_status = "unknown"
            row.last_monitored_at = datetime.now(timezone.utc)
            self.assertEqual(_public_system_settings(row)["connection_status"], "Unverified")
            row.last_monitor_status = "missing"
            self.assertEqual(_public_system_settings(row)["connection_status"], "Error")
            row.monitoring_enabled = False
            self.assertEqual(_public_system_settings(row)["connection_status"], "Unverified")

    def test_websocket_reconnect_replays_authorized_status_snapshot(self):
        self._login_and_configure_for_activity()
        # Starlette's TestClient constructs ws://testserver internally and
        # therefore cannot automatically attach the Secure __Host- cookie.
        # Supply the already-issued cookie to emulate a same-origin WSS browser.
        session_cookie_name = os.getenv("SESSION_COOKIE_NAME", "__Host-opensigweave-session")
        session_cookie = self.client.cookies.get(session_cookie_name)
        self.assertTrue(session_cookie)
        with patch.object(main, "SessionLocal", self.Session):
            for _ in range(2):
                with self.client.websocket_connect("/ws/portal", headers={
                        "Origin": "https://app.example.org",
                        "Host": "app.example.org",
                        "Cookie": f"{session_cookie_name}={session_cookie}"}) as socket:
                    hello = socket.receive_json()
                    self.assertEqual(hello["type"], "ready")
                    self.assertTrue(hello["status_available"])
                    socket.send_json({"type": "subscribe-status"})
                    snapshot = socket.receive_json()
                    self.assertEqual(snapshot["type"], "system-status")
                    self.assertEqual(snapshot["data"]["mail_adapter"]["connection_status"],
                                     "Unverified")

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
        redirect = self.client.get("/")
        self.assertEqual(redirect.status_code, 303)
        self.assertEqual(redirect.headers["location"], "/user")
        response = self.client.get("/admin/system")
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
        self.assertFalse(saved.json()["setup_completed"])
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
