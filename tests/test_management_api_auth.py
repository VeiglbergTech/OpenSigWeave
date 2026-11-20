"""Runtime security/authorization tests for optional administrator machine access."""
import base64
import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-with-more-than-thirty-two-characters")
os.environ.setdefault("ENGINE_API_KEY", "test-engine-api-key-with-more-than-thirty-two-characters")
from auth_config import hash_local_admin_password

os.environ["APP_ALLOWED_HOSTS"] = "signature-test.example.org,app.example.org"
os.environ["LOCAL_ADMIN_EMAIL"] = "admin@example.org"
os.environ["LOCAL_ADMIN_PASSWORD_HASH"] = hash_local_admin_password("correct horse battery staple")

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from database import Base

TEST_MANAGEMENT_KEY = "test-independent-management-key-longer-than-32-chars"


class ManagementKeyAuthorizationTests(unittest.TestCase):
    COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "__Host-opensigweave-session")

    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

        def override_get_db():
            with self.Session() as db:
                yield db

        self.previous_override = main.app.dependency_overrides.get(main.get_db)
        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app, base_url="https://app.example.org")

    def tearDown(self):
        if self.previous_override is None:
            main.app.dependency_overrides.pop(main.get_db, None)
        else:
            main.app.dependency_overrides[main.get_db] = self.previous_override
        self.client.close()
        self.engine.dispose()

    def authenticate(self, groups):
        payload = base64.b64encode(json.dumps({
            "user": {"email": "admin@example.org", "groups": groups},
            "csrf_token": "test-csrf-token",
        }, separators=(",", ":")).encode("utf-8"))
        signed = TimestampSigner(main.session_secret).sign(payload).decode("utf-8")
        self.client.cookies.set(self.COOKIE_NAME, signed)

    def test_unconfigured_and_incorrect_keys_cannot_read_admin_endpoints(self):
        with patch.object(main, "MANAGEMENT_API_KEY", ""):
            self.assertEqual(self.client.get(
                "/api/admin/signatures", headers={"X-Management-Key": TEST_MANAGEMENT_KEY}
            ).status_code, 403)
        with patch.object(main, "MANAGEMENT_API_KEY", TEST_MANAGEMENT_KEY):
            self.assertEqual(self.client.get("/api/admin/signatures").status_code, 403)
            self.assertEqual(self.client.get(
                "/api/admin/signatures", headers={"X-Management-Key": "incorrect"}
            ).status_code, 403)
            self.assertEqual(self.client.get(
                "/api/admin/signatures", headers={"X-Management-Key": main.ENGINE_API_KEY}
            ).status_code, 403)

    def test_key_authorizes_existing_admin_paths_only_and_disables_caching(self):
        with patch.object(main, "MANAGEMENT_API_KEY", TEST_MANAGEMENT_KEY):
            headers = {"X-Management-Key": TEST_MANAGEMENT_KEY}
            for path in ("/api/admin/signatures", "/api/admin/signature-groups",
                         "/api/admin/rules", "/api/admin/schedules",
                         "/api/admin/branding", "/domains/"):
                with self.subTest(path=path):
                    response = self.client.get(path, headers=headers)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertIn("no-store", response.headers.get("cache-control", ""))
            # Credentials are strictly scoped: no automatic browser or personal identity.
            self.assertEqual(self.client.get("/", headers=headers, follow_redirects=False).status_code, 307)
            self.assertEqual(self.client.get("/api/my-signature", headers=headers).status_code, 401)
            self.assertEqual(self.client.get(
                "/api/v1/engine/signatures/user@example.org", headers=headers
            ).status_code, 401)

    def test_key_can_mutate_without_cookie_or_csrf(self):
        endpoint = "/api/admin/signature-groups"
        payload = {"name": "Machine-created group", "domain_ids": [], "is_active": True}
        with patch.object(main, "MANAGEMENT_API_KEY", TEST_MANAGEMENT_KEY):
            self.assertEqual(self.client.post(endpoint, json=payload).status_code, 403)
            self.assertEqual(self.client.post(
                endpoint, json=payload, headers={"X-Management-Key": "invalid"}
            ).status_code, 403)
            created = self.client.post(
                endpoint, json=payload, headers={"X-Management-Key": TEST_MANAGEMENT_KEY}
            )
            self.assertEqual(created.status_code, 201, created.text)
            group_id = created.json()["id"]
            self.assertEqual(self.client.delete(
                f"/api/admin/signature-groups/{group_id}",
                headers={"X-Management-Key": TEST_MANAGEMENT_KEY},
            ).status_code, 200)

    def test_browser_csrf_remains_enforced_when_management_key_is_missing_or_invalid(self):
        self.authenticate(["signature-admins"])
        payload = {"name": "Session-managed group", "domain_ids": [], "is_active": True}
        with patch.object(main, "MANAGEMENT_API_KEY", TEST_MANAGEMENT_KEY):
            self.assertEqual(self.client.post(
                "/api/admin/signature-groups", json=payload
            ).status_code, 403)
            self.assertEqual(self.client.post(
                "/api/admin/signature-groups", json=payload,
                headers={"X-Management-Key": "incorrect"},
            ).status_code, 403)
            self.assertEqual(self.client.post(
                "/api/admin/signature-groups", json=payload,
                headers={"X-CSRF-Token": "test-csrf-token"},
            ).status_code, 201)

    def test_valid_management_key_can_authorize_with_nonadmin_cookie(self):
        self.authenticate(["signature-users"])
        payload = {"name": "Automated group with a user cookie", "domain_ids": []}
        with patch.object(main, "MANAGEMENT_API_KEY", TEST_MANAGEMENT_KEY):
            response = self.client.post(
                "/api/admin/signature-groups", json=payload,
                headers={"X-Management-Key": TEST_MANAGEMENT_KEY},
            )
        self.assertEqual(response.status_code, 201, response.text)


if __name__ == "__main__":
    unittest.main()
