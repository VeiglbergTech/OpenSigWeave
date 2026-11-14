import unittest
from unittest.mock import AsyncMock, patch

from identity.attributes import IdentityAttributes
from identity.providers.authentik import AuthentikAttributeProvider


class FakeResponse:
    status_code = 200

    def json(self):
        return {
            "results": [
                {
                    "name": "Alice Example",
                    "username": "alice",
                    "pk": "user-immutable-42",
                    "email": "alice@example.org",
                    "is_active": True,
                    "attributes": {
                        "title": ["Systems Engineer"],
                        "phone": "+49 123",
                        "department": "Sales",
                    },
                    "groups": ["sales", "signature-users"],
                }
            ]
        }


class FakeClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.request = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def get(self, url, *, params, headers):
        self.request = {"url": url, "params": params, "headers": headers}
        return FakeResponse()


class AuthentikAttributeProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_attributes_are_normalized_and_email_is_a_query_parameter(self):
        provider = AuthentikAttributeProvider(
            base_url="https://auth.example.org/",
            api_token="test-token",
        )
        client = FakeClient()
        with patch("identity.providers.authentik.httpx.AsyncClient", return_value=client):
            result = await provider.get_attributes("alice@example.org")

        self.assertEqual(
            result,
            IdentityAttributes(
                principal_id="authentik:user-immutable-42",
                first_name="Alice",
                last_name="Example",
                title="Systems Engineer",
                phone="+49 123",
                groups=frozenset({"sales", "signature-users"}),
                claims={"username": "alice", "email": "alice@example.org", "name": "Alice Example", "is_active": True},
                attributes={
                    "title": ["Systems Engineer"],
                    "phone": "+49 123",
                    "department": "Sales",
                },
            ),
        )
        self.assertEqual(client.request["params"], {"search": "alice@example.org"})
        self.assertEqual(client.request["url"], "https://auth.example.org/api/v3/core/users/")


    async def test_lookup_requires_exactly_one_active_email_match(self):
        provider = AuthentikAttributeProvider(
            base_url="https://auth.example.org",
            api_token="test-token",
        )
        authoritative_empty_cases = [
            ([{"email": "alice2@example.org", "is_active": True, "name": "Wrong User"}], "no exact match"),
            ([{"email": "alice@example.org", "is_active": False, "name": "Inactive User"}], "inactive account"),
        ]
        for results, label in authoritative_empty_cases:
            with self.subTest(label=label):
                client = FakeClient()
                response = FakeResponse()
                response.json = lambda results=results: {"results": results}
                with patch("identity.providers.authentik.httpx.AsyncClient", return_value=client):
                    with patch.object(client, "get", new=AsyncMock(return_value=response)):
                        self.assertEqual(await provider.get_attributes("alice@example.org"), IdentityAttributes())

        ambiguous = [
            {"email": "alice@example.org", "is_active": True, "name": "Alice One"},
            {"email": "ALICE@example.org", "is_active": True, "name": "Alice Two"},
        ]
        client = FakeClient()
        response = FakeResponse()
        response.json = lambda: {"results": ambiguous}
        with patch("identity.providers.authentik.httpx.AsyncClient", return_value=client):
            with patch.object(client, "get", new=AsyncMock(return_value=response)):
                with self.assertRaises(Exception):
                    await provider.get_attributes("alice@example.org")

    async def test_http_authentik_url_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "HTTPS URL"):
            AuthentikAttributeProvider(base_url="http://auth.example.org", api_token="test-token")

    async def test_authentik_url_with_embedded_credentials_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "HTTPS URL"):
            AuthentikAttributeProvider(base_url="https://user:password@auth.example.org", api_token="test-token")

    async def test_missing_provider_credentials_return_empty_attributes(self):
        provider = AuthentikAttributeProvider(base_url="", api_token="")
        with self.assertRaises(Exception):
            await provider.get_attributes("alice@example.org")

    async def test_successful_empty_attributes_are_a_valid_fresh_result(self):
        provider = AuthentikAttributeProvider(
            base_url="https://auth.example.org", api_token="test-token"
        )
        client = FakeClient()
        response = FakeResponse()
        response.json = lambda: {"results": [{
            "name": "", "pk": "user-empty-42", "email": "alice@example.org", "is_active": True,
            "attributes": {}, "groups": [],
        }]}
        with patch("identity.providers.authentik.httpx.AsyncClient", return_value=client):
            with patch.object(client, "get", new=AsyncMock(return_value=response)):
                result = await provider.get_attributes("alice@example.org")
        self.assertEqual(result, IdentityAttributes(principal_id="authentik:user-empty-42", claims={"email": "alice@example.org", "name": "", "is_active": True}))


class LDAPAttributeProviderTests(unittest.IsolatedAsyncioTestCase):
    def _entry(self, **attributes):
        from types import SimpleNamespace
        return SimpleNamespace(entry_attributes_as_dict=attributes)

    async def test_ldaps_lookup_maps_ad_attributes_and_escapes_email_filter(self):
        from identity.providers.ldap import LDAPAttributeProvider
        entry = self._entry(
            mail=["alice*)(example.org"], givenName=["Alice"], sn=["Example"],
            title=["Systems Engineer"], telephoneNumber=["+49 123"],
            memberOf=["CN=signature-users,OU=Groups,DC=example,DC=org"],
            sAMAccountName=["alice"], distinguishedName=["CN=Alice,OU=Users,DC=example,DC=org"],
            userAccountControl=["512"], objectGUID=bytes.fromhex("00112233445566778899aabbccddeeff"),
        )
        connection = type("ConnectionStub", (), {
            "entries": [entry], "search": lambda self, **kwargs: setattr(self, "search_args", kwargs),
            "unbind": lambda self: None,
        })()
        provider = LDAPAttributeProvider(
            "ldaps://dc.example.org:636", "CN=reader,DC=example,DC=org",
            "secret", "DC=example,DC=org",
        )
        with patch("identity.providers.ldap.ldap3.Server"), patch(
            "identity.providers.ldap.ldap3.Connection", return_value=connection
        ):
            result = await provider.get_attributes("alice*)(example.org")

        self.assertEqual(result.principal_id, "ldap:00112233445566778899aabbccddeeff")
        self.assertEqual(result.first_name, "Alice")
        self.assertEqual(result.last_name, "Example")
        self.assertEqual(result.title, "Systems Engineer")
        self.assertEqual(result.phone, "+49 123")
        self.assertEqual(result.groups, frozenset({"signature-users"}))
        self.assertIn(r"alice\2a\29\28example.org", connection.search_args["search_filter"])
        self.assertEqual(connection.search_args["search_base"], "DC=example,DC=org")

    async def test_no_matching_account_and_disabled_ad_account_clear_attributes(self):
        from identity.providers.ldap import LDAPAttributeProvider
        provider = LDAPAttributeProvider(
            "ldaps://dc.example.org", "reader", "secret", "DC=example,DC=org"
        )
        empty_connection = type("ConnectionStub", (), {
            "entries": [], "search": lambda self, **kwargs: None, "unbind": lambda self: None,
        })()
        with patch("identity.providers.ldap.ldap3.Server"), patch(
            "identity.providers.ldap.ldap3.Connection", return_value=empty_connection
        ):
            self.assertEqual(await provider.get_attributes("missing@example.org"), IdentityAttributes())

        disabled = self._entry(
            mail=["alice@example.org"], givenName=["Alice"], sn=["Example"],
            userAccountControl=["514"],
        )
        disabled_connection = type("ConnectionStub", (), {
            "entries": [disabled], "search": lambda self, **kwargs: None, "unbind": lambda self: None,
        })()
        with patch("identity.providers.ldap.ldap3.Server"), patch(
            "identity.providers.ldap.ldap3.Connection", return_value=disabled_connection
        ):
            self.assertEqual(await provider.get_attributes("alice@example.org"), IdentityAttributes())

    async def test_ldap_requires_ldaps_and_email_filter_placeholder(self):
        from identity.providers.ldap import LDAPAttributeProvider
        with self.assertRaisesRegex(ValueError, "LDAPS URL"):
            LDAPAttributeProvider("ldap://dc.example.org", "reader", "secret", "DC=example,DC=org")
        with self.assertRaisesRegex(ValueError, "placeholder"):
            LDAPAttributeProvider(
                "ldaps://dc.example.org", "reader", "secret", "DC=example,DC=org",
                user_filter="(objectClass=user)",
            )

    async def test_missing_ldap_configuration_is_reported_as_provider_unavailable(self):
        from identity.providers.ldap import LDAPAttributeProvider
        from identity.providers.authentik import IdentityProviderUnavailable
        provider = LDAPAttributeProvider("", "", "", "")
        with self.assertRaises(IdentityProviderUnavailable):
            await provider.get_attributes("alice@example.org")


if __name__ == "__main__":
    unittest.main()



class IdentityProviderRegistryTests(unittest.TestCase):
    def test_provider_config_is_written_private_and_secret_is_not_described(self):
        import os
        import tempfile
        from pathlib import Path
        from identity.registry import IdentityProviderRegistry
        with tempfile.TemporaryDirectory() as tmp:
            registry = IdentityProviderRegistry(Path(tmp) / ".idp")
            registry.save("samba-ad", "Samba Active Directory", "ldap", {
                "LDAP_URL": "ldaps://dc.example.org:636",
                "LDAP_BIND_DN": "CN=reader,DC=example,DC=org",
                "LDAP_BIND_PASSWORD": "very-secret",
                "LDAP_BASE_DN": "DC=example,DC=org",
                "LDAP_USER_FILTER": "(&(objectClass=user)(mail={email}))",
                "LDAP_TIMEOUT": "5",
            })
            config = Path(tmp) / ".idp" / "samba-ad" / ".env"
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertEqual(config.parent.stat().st_mode & 0o777, 0o700)
            safe = registry.describe("samba-ad")
            self.assertNotIn("LDAP_BIND_PASSWORD", safe["settings"])
            self.assertTrue(safe["secret_configured"])
            self.assertNotIn("very-secret", str(safe))

    def test_blank_secret_on_update_preserves_existing_secret(self):
        import tempfile
        from pathlib import Path
        from identity.registry import IdentityProviderRegistry
        with tempfile.TemporaryDirectory() as tmp:
            registry = IdentityProviderRegistry(Path(tmp) / ".idp")
            base = {"AUTHENTIK_URL": "https://auth.example.org", "AUTHENTIK_API_TOKEN": "keep-me"}
            registry.save("auth-main", "Authentik Main", "authentik", base)
            registry.save("auth-main", "Authentik Updated", "authentik", {"AUTHENTIK_URL": "https://auth2.example.org", "AUTHENTIK_API_TOKEN": ""})
            self.assertEqual(registry._configs["auth-main"]["AUTHENTIK_API_TOKEN"], "keep-me")
            self.assertEqual(registry._configs["auth-main"]["AUTHENTIK_URL"], "https://auth2.example.org")

    def test_generic_oidc_provider_stores_client_secret_privately_and_describes_safe_settings(self):
        import tempfile
        from pathlib import Path
        from identity.registry import IdentityProviderRegistry
        with tempfile.TemporaryDirectory() as tmp:
            registry = IdentityProviderRegistry(Path(tmp) / ".idp")
            settings = {
                "OIDC_DISCOVERY_URL": "https://idp.example.org/.well-known/openid-configuration",
                "OIDC_CLIENT_ID": "opensigweave",
                "OIDC_CLIENT_SECRET": "generic-oidc-secret",
                "OIDC_SCOPES": "openid email profile groups",
                "OIDC_REDIRECT_URI": "https://signatures.example.org/auth/oidc-main",
            }
            registry.save("oidc-main", "Company OIDC", "oidc", settings)
            safe = registry.describe("oidc-main")
            self.assertEqual(safe["provider_type"], "oidc")
            self.assertNotIn("OIDC_CLIENT_SECRET", safe["settings"])
            self.assertTrue(safe["secret_configured"])
            self.assertNotIn("generic-oidc-secret", str(safe))
            self.assertEqual(registry.get_config("oidc-main")["OIDC_CLIENT_SECRET"], "generic-oidc-secret")
            self.assertIsNone(registry.get("oidc-main"))

    def test_generic_oidc_provider_requires_complete_https_configuration(self):
        import tempfile
        from identity.registry import IdentityProviderRegistry, ProviderConfigError
        registry = IdentityProviderRegistry(tempfile.mkdtemp())
        with self.assertRaises(ProviderConfigError):
            registry.save("oidc-bad", "Incomplete OIDC", "oidc", {
                "OIDC_DISCOVERY_URL": "https://idp.example.org/.well-known/openid-configuration",
                "OIDC_CLIENT_ID": "client",
            })
        with self.assertRaises(ProviderConfigError):
            registry.save("oidc-http", "HTTP OIDC", "oidc", {
                "OIDC_DISCOVERY_URL": "http://idp.example.org/.well-known/openid-configuration",
                "OIDC_CLIENT_ID": "client",
                "OIDC_CLIENT_SECRET": "secret",
                "OIDC_REDIRECT_URI": "https://signatures.example.org/auth/oidc-http",
            })

    def test_generic_oidc_connection_test_validates_discovery_metadata(self):
        import asyncio
        import tempfile
        from identity.registry import IdentityProviderRegistry
        class DiscoveryResponse:
            status_code = 200
            def json(self):
                return {
                    "issuer": "https://idp.example.org/",
                    "authorization_endpoint": "https://idp.example.org/authorize",
                    "token_endpoint": "https://idp.example.org/token",
                    "jwks_uri": "https://idp.example.org/jwks",
                }
        class DiscoveryClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, exc_type, exc, traceback):
                return False
            async def get(self, url):
                self.url = url
                return DiscoveryResponse()
        with tempfile.TemporaryDirectory() as tmp:
            registry = IdentityProviderRegistry(tmp)
            registry.save("oidc-main", "Generic OIDC", "oidc", {
                "OIDC_DISCOVERY_URL": "https://idp.example.org/.well-known/openid-configuration",
                "OIDC_CLIENT_ID": "client",
                "OIDC_CLIENT_SECRET": "secret",
                "OIDC_REDIRECT_URI": "https://signatures.example.org/auth/oidc-main",
            })
            client = DiscoveryClient()
            with patch("identity.providers.authentik.httpx.AsyncClient", return_value=client):
                self.assertTrue(asyncio.run(registry.test("oidc-main")))
            self.assertEqual(client.url, "https://idp.example.org/.well-known/openid-configuration")

    def test_provider_slug_rejects_path_traversal(self):
        import tempfile
        from identity.registry import IdentityProviderRegistry, ProviderConfigError
        registry = IdentityProviderRegistry(tempfile.mkdtemp())
        with self.assertRaises(ProviderConfigError):
            registry.save("../escape", "Bad", "authentik", {})

    def test_registry_lists_multiple_provider_instances(self):
        import tempfile
        from identity.registry import IdentityProviderRegistry
        registry = IdentityProviderRegistry(tempfile.mkdtemp())
        registry.save("auth-one", "Auth One", "authentik", {"AUTHENTIK_URL": "https://one.example.org", "AUTHENTIK_API_TOKEN": "token"})
        registry.save("auth-two", "Auth Two", "authentik", {"AUTHENTIK_URL": "https://two.example.org", "AUTHENTIK_API_TOKEN": "token"})
        self.assertEqual({item["id"] for item in registry.list_configs()}, {"auth-one", "auth-two"})


class LDAPAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    def _provider(self):
        from identity.providers.ldap import LDAPAttributeProvider
        return LDAPAttributeProvider(
            "ldaps://dc.example.org:636",
            "CN=readonly,DC=example,DC=org",
            "service-secret",
            "DC=example,DC=org",
        )

    async def test_authentication_searches_with_service_account_then_binds_as_user(self):
        from types import SimpleNamespace
        from identity.providers.ldap import LDAPAttributeProvider

        entry = SimpleNamespace(
            entry_dn="CN=Alice,OU=Users,DC=example,DC=org",
            entry_attributes_as_dict={
                "mail": ["alice@example.org"],
                "givenName": ["Alice"],
                "sn": ["Example"],
                "title": ["Systems Engineer"],
                "memberOf": ["CN=signature-users,OU=Groups,DC=example,DC=org"],
                "userAccountControl": ["512"],
            },
        )
        connections = []

        class ConnectionStub:
            def __init__(self, server, **kwargs):
                self.server = server
                self.kwargs = kwargs
                self.entries = [entry] if len(connections) == 0 else []
                connections.append(self)

            def search(self, **kwargs):
                self.search_args = kwargs

            def unbind(self):
                self.unbound = True

        provider = self._provider()
        with patch("identity.providers.ldap.ldap3.Server"), patch(
            "identity.providers.ldap.ldap3.Connection", side_effect=ConnectionStub
        ):
            result = await provider.authenticate("alice@example.org", "user-password")

        self.assertEqual(len(connections), 2)
        self.assertEqual(connections[0].kwargs["user"], "CN=readonly,DC=example,DC=org")
        self.assertEqual(connections[0].kwargs["password"], "service-secret")
        self.assertEqual(connections[1].kwargs["user"], "CN=Alice,OU=Users,DC=example,DC=org")
        self.assertEqual(connections[1].kwargs["password"], "user-password")
        self.assertTrue(connections[0].unbound)
        self.assertTrue(connections[1].unbound)
        self.assertEqual(result.first_name, "Alice")
        self.assertEqual(result.groups, frozenset({"signature-users"}))

    async def test_unknown_or_disabled_ldap_accounts_do_not_attempt_user_bind(self):
        from types import SimpleNamespace

        provider = self._provider()
        empty_connection = SimpleNamespace(entries=[], search=lambda **kwargs: None, unbind=lambda: None)
        with patch("identity.providers.ldap.ldap3.Server"), patch(
            "identity.providers.ldap.ldap3.Connection", return_value=empty_connection
        ) as connection_factory:
            self.assertIsNone(await provider.authenticate("missing@example.org", "password"))
        self.assertEqual(connection_factory.call_count, 1)

        disabled_entry = SimpleNamespace(
            entry_dn="CN=Alice,DC=example,DC=org",
            entry_attributes_as_dict={
                "mail": ["alice@example.org"],
                "userAccountControl": ["514"],
            },
        )
        disabled_connection = SimpleNamespace(
            entries=[disabled_entry],
            search=lambda **kwargs: None,
            unbind=lambda: None,
        )
        with patch("identity.providers.ldap.ldap3.Server"), patch(
            "identity.providers.ldap.ldap3.Connection", return_value=disabled_connection
        ) as connection_factory:
            self.assertIsNone(await provider.authenticate("alice@example.org", "password"))
        self.assertEqual(connection_factory.call_count, 1)


class ManagedOIDCProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_oidc_only_authentik_provider_connection_test_uses_discovery(self):
        import tempfile
        from identity.registry import IdentityProviderRegistry

        class DiscoveryResponse:
            status_code = 200

            def json(self):
                return {
                    "issuer": "https://auth.example.org/application/o/app/",
                    "authorization_endpoint": "https://auth.example.org/application/o/authorize/",
                    "token_endpoint": "https://auth.example.org/application/o/token/",
                    "jwks_uri": "https://auth.example.org/application/o/app/jwks/",
                }

        class DiscoveryClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            async def get(self, url):
                self.url = url
                return DiscoveryResponse()

        client = DiscoveryClient()
        registry = IdentityProviderRegistry(tempfile.mkdtemp())
        registry.save("auth-main", "Authentik Main", "authentik", {
            "OIDC_DISCOVERY_URL": "https://auth.example.org/application/o/app/.well-known/openid-configuration",
            "OIDC_CLIENT_ID": "client-id",
            "OIDC_CLIENT_SECRET": "client-secret",
            "OIDC_REDIRECT_URI": "https://signatures.example.org/auth/auth-main",
        })
        with patch("identity.providers.authentik.httpx.AsyncClient", return_value=client):
            self.assertTrue(await registry.test("auth-main"))
        self.assertEqual(
            registry.get_config("auth-main")["OIDC_CLIENT_SECRET"],
            "client-secret",
        )
        self.assertNotIn("OIDC_CLIENT_SECRET", registry.describe("auth-main")["settings"])

    async def test_managed_oidc_requires_complete_secure_configuration(self):
        import tempfile
        from identity.registry import IdentityProviderRegistry, ProviderConfigError

        registry = IdentityProviderRegistry(tempfile.mkdtemp())
        with self.assertRaises(ProviderConfigError):
            registry.save("auth-main", "Authentik Main", "authentik", {
                "OIDC_DISCOVERY_URL": "http://auth.example.org/.well-known/openid-configuration",
                "OIDC_CLIENT_ID": "client-id",
                "OIDC_CLIENT_SECRET": "client-secret",
                "OIDC_REDIRECT_URI": "https://signatures.example.org/auth/auth-main",
            })
