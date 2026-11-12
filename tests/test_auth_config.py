"""Optional OIDC and standalone local administrator authentication tests."""
import asyncio
import os
import subprocess
import sys
import unittest
from types import SimpleNamespace

from identity.attributes import IdentityAttributes
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


class ManagedLoginRoutingTests(unittest.IsolatedAsyncioTestCase):
    class FakeQuery:
        def __init__(self, result):
            self.result = result

        def filter(self, *args):
            return self

        def first(self):
            return self.result

    class FakeDatabase:
        def __init__(self, domain, provider):
            self.domain = domain
            self.provider = provider

        def query(self, model):
            return ManagedLoginRoutingTests.FakeQuery(self.domain)

        def get(self, model, provider_id):
            return self.provider

    def _request(self, email, password=""):
        return SimpleNamespace(
            session={"login_csrf": "one-time-token"},
            headers={"content-type": "application/x-www-form-urlencoded"},
            body=AsyncMock(return_value=urlencode({
                "csrf_token": "one-time-token",
                "email": email,
                "password": password,
            }).encode("utf-8")),
        )

    async def test_login_routes_to_ldap_and_builds_user_session(self):
        domain = SimpleNamespace(authentication_provider_id="ad-main", is_active=True)
        provider = SimpleNamespace(is_enabled=True, provider_type="ldap")
        db = self.FakeDatabase(domain, provider)
        request = self._request("alice@example.org", "directory-password")
        identity = IdentityAttributes(
            first_name="Alice",
            last_name="Example",
            title="Systems Engineer",
            phone="+49 123",
            groups=frozenset({"signature-users"}),
            claims={"distinguishedname": "CN=Alice,OU=Users,DC=example,DC=org"},
        )
        with (
            patch.object(main, "LOCAL_ADMIN_CONFIGURED", False),
            patch.object(main.identity_provider_registry, "authenticate", new=AsyncMock(return_value=identity)) as authenticate,
        ):
            response = await main.login_submit(request, db=db)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")
        authenticate.assert_awaited_once_with("ad-main", "alice@example.org", "directory-password")
        self.assertEqual(request.session["user"]["auth_source"], "ldap")
        self.assertEqual(request.session["user"]["auth_provider_id"], "ad-main")
        self.assertIn("signature-users", request.session["user"]["groups"])

    async def test_login_starts_domain_assigned_oidc_provider(self):
        domain = SimpleNamespace(authentication_provider_id="auth-main", is_active=True)
        provider = SimpleNamespace(is_enabled=True, provider_type="authentik")
        db = self.FakeDatabase(domain, provider)
        request = self._request("alice@example.org")
        client = SimpleNamespace(authorize_redirect=AsyncMock(return_value=main.RedirectResponse("/provider", status_code=302)))
        config = {"OIDC_REDIRECT_URI": "https://signatures.example.org/auth/auth-main"}
        with (
            patch.object(main, "LOCAL_ADMIN_CONFIGURED", False),
            patch.object(main, "managed_oidc_client", return_value=(client, config)),
        ):
            response = await main.login_submit(request, db=db)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            request.session["pending_managed_login"],
            {"email": "alice@example.org", "provider_id": "auth-main"},
        )
        client.authorize_redirect.assert_awaited_once()

    async def test_managed_oidc_callback_rechecks_domain_assignment_and_email(self):
        domain = SimpleNamespace(authentication_provider_id="auth-main", is_active=True)
        provider = SimpleNamespace(is_enabled=True, provider_type="authentik")
        db = self.FakeDatabase(domain, provider)
        request = SimpleNamespace(session={
            "pending_managed_login": {"email": "alice@example.org", "provider_id": "auth-main"}
        })
        token = {"userinfo": {
            "sub": "user-123",
            "email": "alice@example.org",
            "email_verified": True,
            "groups": ["signature-users"],
            "name": "Alice Example",
        }}
        client = SimpleNamespace(authorize_access_token=AsyncMock(return_value=token))
        with patch.object(main, "managed_oidc_client", return_value=(client, {})):
            response = await main.managed_oidc_callback("auth-main", request, db=db)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")
        self.assertEqual(request.session["user"]["email"], "alice@example.org")
        self.assertEqual(request.session["user"]["auth_provider_id"], "auth-main")


class ManagedLogoutTests(unittest.TestCase):
    def test_managed_oidc_logout_uses_discovered_end_session_endpoint(self):
        request = SimpleNamespace(session={
            "user": {"auth_source": "oidc", "auth_provider_id": "auth-main"}
        })
        client = SimpleNamespace(load_server_metadata=AsyncMock(return_value={
            "end_session_endpoint": "https://auth.example.org/application/o/app/end-session/"
        }))
        config = {
            "OIDC_REDIRECT_URI": "https://signatures.example.org/auth/auth-main",
            "OIDC_POST_LOGOUT_REDIRECT_URI": "https://signatures.example.org/login",
        }
        with patch.object(main, "managed_oidc_client", return_value=(client, config)):
            response = asyncio.run(main.logout(request))
        self.assertEqual(response["logout_url"].split("?", 1)[0], "https://auth.example.org/application/o/app/end-session/")
        self.assertIn("post_logout_redirect_uri=https%3A%2F%2Fsignatures.example.org%2Flogin", response["logout_url"])
        self.assertEqual(request.session, {})

    def test_local_administrator_logout_does_not_redirect_to_legacy_oidc(self):
        request = SimpleNamespace(session={
            "user": {"auth_source": "local", "email": "admin@example.org"}
        })
        with patch.object(main, "OIDC_ENABLED", True):
            response = asyncio.run(main.logout(request))
        self.assertEqual(response, {"logout_url": "/login"})
        self.assertEqual(request.session, {})


class ManagedOIDCLoginButtonTests(unittest.IsolatedAsyncioTestCase):
    class ProviderQuery:
        def __init__(self, providers):
            self.providers = providers

        def filter(self, *args):
            return self

        def all(self):
            return self.providers

    class ProviderDatabase:
        def __init__(self, providers, selected):
            self.providers = providers
            self.selected = selected

        def query(self, model):
            return ManagedOIDCLoginButtonTests.ProviderQuery(self.providers)

        def get(self, model, provider_id):
            return self.selected

    async def test_login_page_lists_only_enabled_and_fully_configured_oidc_providers(self):
        providers = [
            SimpleNamespace(id="auth-main", display_name="Authentik Main"),
            SimpleNamespace(id="auth-incomplete", display_name="Incomplete Provider"),
        ]
        db = self.ProviderDatabase(providers, None)
        complete = {
            "OIDC_DISCOVERY_URL": "https://auth.example.org/.well-known/openid-configuration",
            "OIDC_CLIENT_ID": "client",
            "OIDC_CLIENT_SECRET": "secret",
            "OIDC_REDIRECT_URI": "https://signatures.example.org/auth/auth-main",
        }
        with patch.object(
            main.identity_provider_registry,
            "get_config",
            side_effect=[complete, {"OIDC_DISCOVERY_URL": "https://auth.example.org/.well-known/openid-configuration"}],
        ):
            result = main.configured_login_providers(db)
        self.assertEqual(result, [{"id": "auth-main", "display_name": "Authentik Main"}])

    async def test_oidc_button_starts_provider_flow_without_email_preselection(self):
        provider = SimpleNamespace(is_enabled=True, provider_type="authentik")
        db = self.ProviderDatabase([], provider)
        request = SimpleNamespace(session={})
        client = SimpleNamespace(authorize_redirect=AsyncMock(return_value=main.RedirectResponse("/auth", status_code=302)))
        config = {"OIDC_REDIRECT_URI": "https://signatures.example.org/auth/auth-main"}
        with patch.object(main, "managed_oidc_client", return_value=(client, config)):
            response = await main.managed_oidc_login("auth-main", request, db)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(request.session["pending_managed_login"], {"provider_id": "auth-main"})
        client.authorize_redirect.assert_awaited_once()

    async def test_oidc_button_callback_routes_by_verified_email_domain(self):
        domain = SimpleNamespace(authentication_provider_id="auth-main", is_active=True)
        provider = SimpleNamespace(is_enabled=True, provider_type="authentik")
        db = ManagedLoginRoutingTests.FakeDatabase(domain, provider)
        request = SimpleNamespace(session={
            "pending_managed_login": {"provider_id": "auth-main"}
        })
        token = {"userinfo": {
            "sub": "user-456",
            "email": "alice@example.org",
            "email_verified": True,
            "groups": ["signature-users"],
            "name": "Alice Example",
        }}
        client = SimpleNamespace(authorize_access_token=AsyncMock(return_value=token))
        with patch.object(main, "managed_oidc_client", return_value=(client, {})):
            response = await main.managed_oidc_callback("auth-main", request, db=db)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(request.session["user"]["email"], "alice@example.org")
