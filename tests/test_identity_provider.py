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
            "name": "", "email": "alice@example.org", "is_active": True,
            "attributes": {}, "groups": [],
        }]}
        with patch("identity.providers.authentik.httpx.AsyncClient", return_value=client):
            with patch.object(client, "get", new=AsyncMock(return_value=response)):
                result = await provider.get_attributes("alice@example.org")
        self.assertEqual(result, IdentityAttributes(claims={"email": "alice@example.org", "name": "", "is_active": True}))


class LDAPAttributeProviderTests(unittest.IsolatedAsyncioTestCase):
    def _entry(self, **attributes):
        from types import SimpleNamespace
        return SimpleNamespace(entry_attributes_as_dict=attributes)

    async def test_ldaps_lookup_maps_ad_attributes_and_escapes_email_filter(self):
        from identity.providers.ldap import LDAPAttributeProvider
        entry = self._entry(
            mail=["alice@example.org"], givenName=["Alice"], sn=["Example"],
            title=["Systems Engineer"], telephoneNumber=["+49 123"],
            memberOf=["CN=signature-users,OU=Groups,DC=example,DC=org"],
            sAMAccountName=["alice"], distinguishedName=["CN=Alice,OU=Users,DC=example,DC=org"],
            userAccountControl=["512"],
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
