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
        cases = [
            ([{"email": "alice2@example.org", "is_active": True, "name": "Wrong User"}], "no exact match"),
            ([{"email": "alice@example.org", "is_active": False, "name": "Inactive User"}], "inactive account"),
            ([
                {"email": "alice@example.org", "is_active": True, "name": "Alice One"},
                {"email": "ALICE@example.org", "is_active": True, "name": "Alice Two"},
            ], "ambiguous matches"),
        ]
        for results, label in cases:
            with self.subTest(label=label):
                client = FakeClient()
                response = FakeResponse()
                response.json = lambda results=results: {"results": results}
                with patch("identity.providers.authentik.httpx.AsyncClient", return_value=client):
                    with patch.object(client, "get", new=AsyncMock(return_value=response)):
                        actual = await provider.get_attributes("alice@example.org")
                self.assertEqual(actual, IdentityAttributes())

    async def test_missing_provider_credentials_return_empty_attributes(self):
        provider = AuthentikAttributeProvider(base_url="", api_token="")
        self.assertEqual(await provider.get_attributes("alice@example.org"), IdentityAttributes())


if __name__ == "__main__":
    unittest.main()
