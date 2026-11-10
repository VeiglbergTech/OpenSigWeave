import unittest
from unittest.mock import patch

from identity.attributes import IdentityAttributes
from identity.providers.authentik import AuthentikAttributeProvider


class FakeResponse:
    status_code = 200

    def json(self):
        return {
            "results": [
                {
                    "name": "Alice Example",
                    "attributes": {
                        "title": ["Systems Engineer"],
                        "phone": "+49 123",
                    },
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
            result = await provider.get_attributes("alice+tag@example.org")

        self.assertEqual(
            result,
            IdentityAttributes(
                first_name="Alice",
                last_name="Example",
                title="Systems Engineer",
                phone="+49 123",
            ),
        )
        self.assertEqual(client.request["params"], {"search": "alice+tag@example.org"})
        self.assertEqual(client.request["url"], "https://auth.example.org/api/v3/core/users/")

    async def test_missing_provider_credentials_return_empty_attributes(self):
        provider = AuthentikAttributeProvider(base_url="", api_token="")
        self.assertEqual(await provider.get_attributes("alice@example.org"), IdentityAttributes())


if __name__ == "__main__":
    unittest.main()
