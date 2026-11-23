"""Retired host backup web routes remain authenticated but do no host work."""
import base64
import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-with-more-than-thirty-two-characters")
os.environ.setdefault("ENGINE_API_KEY", "test-engine-api-key-with-more-than-thirty-two-characters")
os.environ["APP_ALLOWED_HOSTS"] = "signature-test.example.org,app.example.org"

from auth_config import hash_local_admin_password
os.environ["LOCAL_ADMIN_EMAIL"] = "admin@example.org"
os.environ["LOCAL_ADMIN_PASSWORD_HASH"] = hash_local_admin_password("correct horse battery staple")

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
import main


class BackupAdminApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app, base_url="https://app.example.org")

    def tearDown(self):
        self.client.close()

    def authenticate(self, groups, csrf="backup-test-csrf"):
        value = base64.b64encode(json.dumps({
            "user": {"email": "admin@example.org", "groups": groups},
            "csrf_token": csrf,
        }, separators=(",", ":")).encode("utf-8"))
        cookie = TimestampSigner(main.session_secret).sign(value).decode("utf-8")
        self.client.cookies.set(os.getenv("SESSION_COOKIE_NAME", "__Host-opensigweave-session"), cookie)

    def legacy_cases(self):
        return [
            ("GET", "/api/admin/backups", None),
            ("PUT", "/api/admin/backups/settings",
             {"directory": "/mnt/x", "automatic": False, "interval_hours": 24, "keep": 5}),
            ("POST", "/api/admin/backups/create", None),
            ("DELETE", "/api/admin/backups/opensigweave-20261122T190000Z.age", {"confirm": "DELETE"}),
            ("POST", "/api/admin/backups/opensigweave-20261122T190000Z.age/restore",
             {"confirm": "RESTORE TO NEW DEPLOYMENT"}),
            ("GET", "/api/admin/backups/opensigweave-20261122T190000Z.age/download", None),
        ]

    def test_legacy_routes_require_admin_and_never_contact_host(self):
        for groups in ([], ["signature-users"]):
            self.authenticate(groups)
            for method, path, payload in self.legacy_cases():
                with self.subTest(groups=groups, path=path):
                    response = self.client.request(method, path, json=payload,
                        headers={"X-CSRF-Token": "backup-test-csrf"} if method != "GET" else {})
                    self.assertEqual(response.status_code, 403, response.text)

    def test_retired_operator_endpoints_return_410_without_leaking_secrets(self):
        self.authenticate(["signature-admins"])
        for method, path, payload in self.legacy_cases():
            with self.subTest(path=path):
                response = self.client.request(method, path, json=payload,
                    headers={"X-CSRF-Token": "backup-test-csrf"} if method != "GET" else {})
                self.assertEqual(response.status_code, 410, response.text)
                self.assertIn("retired", response.text.lower())
                self.assertNotIn("socket", response.text.lower())
                self.assertIn("no-store", response.headers.get("cache-control", "").lower())

    def test_retired_operator_write_endpoints_require_csrf(self):
        self.authenticate(["signature-admins"])
        for method, path, payload in self.legacy_cases():
            if method == "GET":
                continue
            with self.subTest(path=path):
                response = self.client.request(method, path, json=payload)
                self.assertEqual(response.status_code, 403, response.text)


if __name__ == "__main__":
    unittest.main()
