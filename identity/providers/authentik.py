"""Authentik-backed employee attribute provider."""
import logging
from urllib.parse import urlsplit

import httpx2 as httpx

from identity.attributes import IdentityAttributes
from identity.providers.base import IdentityProviderUnavailable

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

    async def list_users(self, domain: str, max_results: int = 2000):
        """Bounded Authentik API listing; never expose tokens or arbitrary user claims."""
        import re

        if not self.base_url or not self.api_token:
            raise IdentityProviderUnavailable("Authentik user listing is not configured")
        if not isinstance(domain, str) or not re.fullmatch(r"[a-zA-Z0-9.-]{1,253}", domain):
            raise ValueError("Invalid directory domain")
        if max_results < 1 or max_results > 2000:
            raise ValueError("Invalid directory limit")
        users = {}
        page_size = min(100, max_results)
        max_pages = (max_results + page_size - 1) // page_size
        truncated = False
        headers = {"Authorization": f"Bearer {self.api_token}", "Accept": "application/json"}
        url = f"{self.base_url}/api/v3/core/users/"
        try:
            async with httpx.AsyncClient(verify=True, timeout=httpx.Timeout(self.timeout)) as client:
                for page in range(1, max_pages + 1):
                    response = await client.get(
                        url, params={"page": page, "page_size": page_size}, headers=headers,
                    )
                    if response.status_code != 200:
                        raise IdentityProviderUnavailable(
                            f"Authentik listing returned HTTP {response.status_code}"
                        )
                    payload = response.json()
                    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                        raise IdentityProviderUnavailable("Invalid Authentik user listing response")
                    results = payload["results"]
                    for item in results:
                        if not isinstance(item, dict):
                            continue
                        email = item.get("email")
                        if not isinstance(email, str):
                            continue
                        email = email.strip().casefold()
                        if not email.endswith("@" + domain.casefold()) or email.count("@") != 1:
                            continue
                        users[email] = {
                            "email": email,
                            "display_name": str(item.get("name") or "")[:256],
                            "disabled": item.get("is_active") is False,
                        }
                    pagination = payload.get("pagination")
                    has_next = (bool(pagination.get("next")) if isinstance(pagination, dict)
                                else len(results) >= page_size)
                    if not has_next:
                        return list(users.values()), False
                    if page == max_pages:
                        truncated = True
            return list(users.values()), truncated
        except IdentityProviderUnavailable:
            raise
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            logger.warning("Authentik directory listing failed: %s", type(exc).__name__)
            raise IdentityProviderUnavailable("Authentik user listing failed") from exc

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
            stable_pk = user.get("pk")
            if not isinstance(stable_pk, (str, int)) or not str(stable_pk).strip():
                raise IdentityProviderUnavailable("Authentik user response omitted immutable user pk")
            return IdentityAttributes(
                principal_id=f"authentik:{stable_pk}",
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
