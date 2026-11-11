"""Authentik-backed employee attribute provider."""
import logging

import httpx

from identity.attributes import IdentityAttributes

logger = logging.getLogger(__name__)


def _attribute_value(value) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    if value is None:
        return ""
    return str(value)


class AuthentikAttributeProvider:
    def __init__(self, base_url: str, api_token: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.timeout = timeout

    async def get_attributes(self, email: str) -> IdentityAttributes:
        if not self.base_url or not self.api_token:
            return IdentityAttributes()

        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json",
        }
        url = f"{self.base_url}/api/v3/core/users/"

        try:
            async with httpx.AsyncClient(
                verify=True,
                timeout=httpx.Timeout(self.timeout),
            ) as client:
                response = await client.get(
                    url,
                    params={"search": email},
                    headers=headers,
                )
            if response.status_code != 200:
                logger.warning("Authentik attribute lookup returned HTTP %s", response.status_code)
                return IdentityAttributes()

            payload = response.json()
            if not isinstance(payload, dict):
                return IdentityAttributes()
            results = payload.get("results", [])
            if not isinstance(results, list) or not results or not isinstance(results[0], dict):
                return IdentityAttributes()

            user = results[0]
            name_parts = str(user.get("name") or "").split()
            attributes = user.get("attributes")
            if not isinstance(attributes, dict):
                attributes = {}

            first_name = name_parts[0] if name_parts else ""
            last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

            raw_groups = user.get("groups", [])
            groups = frozenset(
                str(group.get("name") or group.get("pk") or "")
                if isinstance(group, dict) else str(group)
                for group in raw_groups
                if isinstance(group, (str, int, dict))
            ) if isinstance(raw_groups, list) else frozenset()
            groups = frozenset(group for group in groups if group)

            claim_keys = ("username", "email", "name", "is_active", "uid", "path", "type")
            claims = {key: user[key] for key in claim_keys if key in user}

            return IdentityAttributes(
                first_name=first_name,
                last_name=last_name,
                title=_attribute_value(attributes.get("title")),
                phone=_attribute_value(attributes.get("phone")),
                groups=groups,
                claims=claims,
                attributes=attributes,
            )
        except (httpx.HTTPError, ValueError, TypeError):
            logger.warning("Authentik attribute lookup failed; using empty profile attributes")
            return IdentityAttributes()
