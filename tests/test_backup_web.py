"""Authorized web backup management uses the isolated host operator only."""
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

    def test_all_routes_reject_unauthorized_user(self):
        cases = [
            ("get", "/api/admin/backups", None),
            ("put", "/api/admin/backups/settings", {"directory": "/mnt/x", "automatic": False, "interval_hours": 24, "keep": 5}),
            ("post", "/api/admin/backups/create", None),
            ("delete", "/api/admin/backups/opensigweave-20261122T190000Z.age", {"confirm": "DELETE"}),
            ("post", "/api/admin/backups/opensigweave-20261122T190000Z.age/restore", {"confirm": "RESTORE TO NEW DEPLOYMENT"}),
            ("get", "/api/admin/backups/opensigweave-20261122T190000Z.age/download", None),
        ]
        for groups in ([], ["signature-users"]):
            self.authenticate(groups)
            with patch.object(main, "backup_request") as operator, patch.object(main, "backup_download") as download:
                for method, path, payload in cases:
                    with self.subTest(groups=groups, path=path):
                        response = self.client.request(
                            method.upper(), path, json=payload,
                            headers={"X-CSRF-Token": "backup-test-csrf"} if method != "get" else {}
                        )
                        self.assertEqual(response.status_code, 403, response.text)
                operator.assert_not_called()
                download.assert_not_called()

    def test_csrf_and_host_operator_dispatch(self):
        self.authenticate(["signature-admins"])
        settings = {"directory": "/mnt/backup", "automatic": True, "interval_hours": 6, "keep": 9}
        with patch.object(main, "backup_request", return_value={"job": {"state": "pending"}}) as operator:
            self.assertEqual(self.client.post("/api/admin/backups/create").status_code, 403)
            response = self.client.post("/api/admin/backups/create", headers={"X-CSRF-Token": "backup-test-csrf"})
            self.assertEqual(response.status_code, 202, response.text)
            self.assertEqual(operator.call_args.args, ("create",))
            response = self.client.put("/api/admin/backups/settings", json=settings,
                                       headers={"X-CSRF-Token": "backup-test-csrf"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(operator.call_args.kwargs, {"settings": settings})
            response = self.client.request("DELETE", "/api/admin/backups/opensigweave-20261122T190000Z.age",
                                           json={"confirm": "DELETE"},
                                           headers={"X-CSRF-Token": "backup-test-csrf"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(operator.call_args.args, ("delete",))
            self.assertEqual(operator.call_args.kwargs["confirm"], "DELETE")
            response = self.client.post("/api/admin/backups/opensigweave-20261122T190000Z.age/restore",
                                        json={"confirm": "RESTORE TO NEW DEPLOYMENT"},
                                        headers={"X-CSRF-Token": "backup-test-csrf"})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(operator.call_args.args, ("restore",))
            self.assertEqual(operator.call_args.kwargs["confirm"], "RESTORE TO NEW DEPLOYMENT")

    def test_download_requires_admin_and_does_not_cache(self):
        name = "opensigweave-20261122T190000Z.age"
        self.authenticate(["signature-admins"])
        with patch.object(main, "backup_download", return_value=(11, iter([b"age-content"]))) as download:
            response = self.client.get("/api/admin/backups/" + name + "/download")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, b"age-content")
        self.assertEqual(response.headers["Content-Length"], "11")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn(name, response.headers["Content-Disposition"])
        download.assert_called_once_with(name)
        self.assertEqual(self.client.get("/api/admin/backups/unsafe.txt/download").status_code, 404)

    def test_unconfigured_host_reports_unavailable_without_leaks(self):
        self.authenticate(["signature-admins"])
        with patch.object(main, "backup_request", side_effect=main.BackupControlError("socket missing")):
            response = self.client.get("/api/admin/backups")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["available"])
        self.assertNotIn("socket missing", response.text)


if __name__ == "__main__":
    unittest.main()
