"""Optional OIDC and standalone local administrator authentication tests."""
import asyncio
import os
import subprocess
import sys
import unittest
from types import SimpleNamespace
from urllib.parse import urlencode
from unittest.mock import AsyncMock, patch

os.environ["OIDC_ENABLED"] = "true"
os.environ.setdefault("SECRET_KEY", "test-secret-key-with-more-than-thirty-two-characters")
os.environ.setdefault("ENGINE_API_KEY", "test-engine-api-key-with-more-than-thirty-two-characters")
os.environ.setdefault("OIDC_DISCOVERY_URL", "https://idp.example/application/o/opensigweave/.well-known/openid-configuration")
os.environ.setdefault("OIDC_REDIRECT_URI", "https://app.example.org/auth")
os.environ.setdefault("OIDC_CLIENT_ID", "opensigweave-test")
os.environ.setdefault("OIDC_CLIENT_SECRET", "opensigweave-test-client-secret")

from auth_config import (
    hash_local_admin_password,
    is_valid_email,
    is_valid_password_hash,
    oidc_enabled_from_env,
    verify_local_admin_password,
)
import main


class AuthenticationConfigurationTests(unittest.TestCase):
    def test_oidc_flag_accepts_explicit_boolean_values(self):
        self.assertTrue(oidc_enabled_from_env("true"))
        self.assertTrue(oidc_enabled_from_env("YES"))
        self.assertFalse(oidc_enabled_from_env("false"))
        self.assertFalse(oidc_enabled_from_env("0"))
        with self.assertRaises(RuntimeError):
            oidc_enabled_from_env("sometimes")

    def test_local_admin_password_uses_scrypt_hash(self):
        encoded = hash_local_admin_password("correct horse battery staple")
        self.assertTrue(is_valid_password_hash(encoded))
        self.assertTrue(verify_local_admin_password("correct horse battery staple", encoded))
        self.assertFalse(verify_local_admin_password("incorrect password", encoded))
        self.assertFalse(verify_local_admin_password("correct horse battery staple", "not-a-hash"))
        with self.assertRaises(ValueError):
            hash_local_admin_password("short")

    def test_local_admin_email_validation(self):
        self.assertTrue(is_valid_email("admin@example.org"))
        self.assertFalse(is_valid_email("not-an-email"))
        self.assertFalse(is_valid_email("admin@localhost"))

    def test_local_login_establishes_admin_session(self):
        password_hash = hash_local_admin_password("correct horse battery staple")
        request = SimpleNamespace(
            session={"login_csrf": "one-time-token"},
            headers={"content-type": "application/x-www-form-urlencoded"},
            body=AsyncMock(return_value=urlencode({
                "csrf_token": "one-time-token",
                "email": "ADMIN@example.org",
                "password": "correct horse battery staple",
            }).encode("utf-8")),
        )
        with (
            patch.object(main, "OIDC_ENABLED", False),
            patch.object(main, "LOCAL_ADMIN_EMAIL", "admin@example.org"),
            patch.object(main, "LOCAL_ADMIN_PASSWORD_HASH", password_hash),
            patch.object(main, "LOCAL_ADMIN_CONFIGURED", True),
        ):
            response = asyncio.run(main.login_submit(request))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")
        self.assertEqual(request.session["user"]["email"], "admin@example.org")
        self.assertIn("signature-admins", request.session["user"]["groups"])
        self.assertTrue(request.session.get("csrf_token"))

    def test_local_login_rejects_bad_password_and_rotates_csrf(self):
        password_hash = hash_local_admin_password("correct horse battery staple")
        request = SimpleNamespace(
            session={"login_csrf": "one-time-token"},
            headers={"content-type": "application/x-www-form-urlencoded"},
            body=AsyncMock(return_value=urlencode({
                "csrf_token": "one-time-token",
                "email": "admin@example.org",
                "password": "wrong password",
            }).encode("utf-8")),
        )
        with (
            patch.object(main, "OIDC_ENABLED", False),
            patch.object(main, "LOCAL_ADMIN_EMAIL", "admin@example.org"),
            patch.object(main, "LOCAL_ADMIN_PASSWORD_HASH", password_hash),
            patch.object(main, "LOCAL_ADMIN_CONFIGURED", True),
        ):
            response = asyncio.run(main.login_submit(request))
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("user", request.session)
        self.assertTrue(request.session.get("login_csrf"))

    def test_local_login_rejects_invalid_csrf(self):
        request = SimpleNamespace(
            session={"login_csrf": "expected"},
            headers={"content-type": "application/x-www-form-urlencoded"},
            body=AsyncMock(return_value=urlencode({
                "csrf_token": "attacker-controlled",
                "email": "admin@example.org",
                "password": "irrelevant",
            }).encode("utf-8")),
        )
        with patch.object(main, "OIDC_ENABLED", False):
            with self.assertRaises(main.HTTPException) as raised:
                asyncio.run(main.login_submit(request))
        self.assertEqual(raised.exception.status_code, 403)

    def test_import_without_oidc_configuration_registers_no_oidc_callback(self):
        password_hash = hash_local_admin_password("correct horse battery staple")
        env = os.environ.copy()
        for name in (
            "OIDC_DISCOVERY_URL", "OIDC_REDIRECT_URI", "OIDC_CLIENT_ID",
            "OIDC_CLIENT_SECRET", "CLIENT_ID", "CLIENT_SECRET",
            "AUTHENTIK_URL", "AUTHENTIK_SLUG", "AUTHENTIK_API_TOKEN",
        ):
            env[name] = ""
        env.update({
            "OIDC_ENABLED": "false",
            "LOCAL_ADMIN_EMAIL": "admin@example.org",
            "LOCAL_ADMIN_PASSWORD_HASH": password_hash,
            "APP_ALLOWED_HOSTS": "signature-test.example.org",
            "SECRET_KEY": "test-secret-key-with-more-than-thirty-two-characters",
            "ENGINE_API_KEY": "test-engine-api-key-with-more-than-thirty-two-characters",
            "DATABASE_URL": "sqlite:///:memory:",
        })
        code = (
            "import main; "
            "assert main.OIDC_ENABLED is False; "
            "assert main.attribute_provider is None; "
            "assert not any(getattr(route, 'path', None) == '/auth' for route in main.app.routes)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
