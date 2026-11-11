"""Authentik-backed employee attribute provider."""
import logging
from urllib.parse import urlsplit

import httpx2 as httpx

from identity.attributes import IdentityAttributes

logger = logging.getLogger(__name__)


class IdentityProviderUnavailable(RuntimeError):
    """Raised when a fresh identity lookup could not be completed reliably."""


def _attribute_value(value) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    if value is None:
        return ""
    return str(value)


class AuthentikAttributeProvider:
    def __init__(self, base_url: str, api_token: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        if self.base_url:
            parsed_url = urlsplit(self.base_url)
            if (
                parsed_url.scheme != "https"
                or not parsed_url.hostname
                or parsed_url.username is not None
                or parsed_url.password is not None
                or parsed_url.query
                or parsed_url.fragment
            ):
                raise ValueError(
                    "AUTHENTIK_URL must be an HTTPS URL without embedded credentials, query or fragment"
                )
        self.api_token = api_token
        self.timeout = timeout

    async def get_attributes(self, email: str) -> IdentityAttributes:
        if not self.base_url or not self.api_token:
            raise IdentityProviderUnavailable("Authentik attribute provider is not configured")

        headers = {"Authorization": f"Bearer {self.api_token}", "Accept": "application/json"}
        url = f"{self.base_url}/api/v3/core/users/"
        try:
            async with httpx.AsyncClient(verify=True, timeout=httpx.Timeout(self.timeout)) as client:
                response = await client.get(url, params={"search": email}, headers=headers)
            if response.status_code != 200:
                raise IdentityProviderUnavailable(f"Authentik returned HTTP {response.status_code}")
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise IdentityProviderUnavailable("Authentik returned an invalid user response")
            requested_email = email.strip().casefold()
            matches = [item for item in payload["results"] if isinstance(item, dict)
                       and isinstance(item.get("email"), str)
                       and item["email"].strip().casefold() == requested_email]
            if not matches:
                # A successful lookup with no matching account is authoritative.
                # Clear cached groups and claims instead of reusing stale access data.
                return IdentityAttributes()
            if len(matches) > 1:
                raise IdentityProviderUnavailable("Authentik returned ambiguous matching accounts")

            user = matches[0]
            if user.get("is_active") is False:
                # A confirmed deactivation must invalidate cached profile and group data.
                return IdentityAttributes()
            if user.get("is_active") is not True:
                raise IdentityProviderUnavailable("Authentik response omitted a valid account status")
            name_parts = str(user.get("name") or "").split()
            attributes = user.get("attributes")
            if not isinstance(attributes, dict):
                attributes = {}
            raw_groups = user.get("groups", [])
            groups = frozenset(
                str(group.get("name") or group.get("pk") or "") if isinstance(group, dict) else str(group)
                for group in raw_groups if isinstance(group, (str, int, dict))
            ) if isinstance(raw_groups, list) else frozenset()
            groups = frozenset(group for group in groups if group)
            claim_keys = ("username", "email", "name", "is_active", "uid", "path", "type")
            claims = {key: user[key] for key in claim_keys if key in user}
            return IdentityAttributes(
                first_name=name_parts[0] if name_parts else "",
                last_name=" ".join(name_parts[1:]) if len(name_parts) > 1 else "",
                title=_attribute_value(attributes.get("title")),
                phone=_attribute_value(attributes.get("phone")),
                groups=groups,
                claims=claims,
                attributes=attributes,
            )
        except IdentityProviderUnavailable:
            raise
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            logger.warning("Authentik attribute lookup failed: %s", type(exc).__name__)
            raise IdentityProviderUnavailable("Authentik attribute lookup failed") from exc
