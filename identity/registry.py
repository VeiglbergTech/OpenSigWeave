"""Filesystem-backed, reloadable identity-provider configuration registry.

Each provider owns one private .idp/<slug>/.env file. Secrets never enter
PostgreSQL or API responses. OIDC authentication remains separate.
"""
from __future__ import annotations

import asyncio
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any
from dotenv import dotenv_values

from identity.providers.authentik import AuthentikAttributeProvider
from identity.providers.ldap import LDAPAttributeProvider

_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_COMMON = {"IDP_TYPE", "IDP_DISPLAY_NAME", "IDP_ENABLED"}
_FIELDS = {
    "authentik": {"AUTHENTIK_URL", "AUTHENTIK_API_TOKEN", "AUTHENTIK_TIMEOUT", "OIDC_DISCOVERY_URL", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "OIDC_SCOPES", "OIDC_REDIRECT_URI", "OIDC_POST_LOGOUT_REDIRECT_URI"},
    "ldap": {"LDAP_URL", "LDAP_BIND_DN", "LDAP_BIND_PASSWORD", "LDAP_BASE_DN",
             "LDAP_USER_FILTER", "LDAP_TIMEOUT", "LDAP_CA_CERT_FILE"},
}


class ProviderConfigError(ValueError):
    pass


def _dotenv_quote(value: str) -> str:
    # JSON string syntax is accepted by python-dotenv and safely preserves
    # whitespace, quotes, backslashes and comment characters.
    import json
    return json.dumps(value, ensure_ascii=False)


class IdentityProviderRegistry:
    def __init__(self, root: str | Path = ".idp"):
        self.root = Path(root).resolve()
        self._providers: dict[str, Any] = {}
        self._configs: dict[str, dict[str, str]] = {}

    @staticmethod
    def validate_slug(slug: str) -> str:
        if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
            raise ProviderConfigError("Provider name must be a lowercase slug (a-z, 0-9, hyphens).")
        return slug

    def _directory(self, slug: str) -> Path:
        slug = self.validate_slug(slug)
        path = self.root / slug
        if path.parent != self.root:
            raise ProviderConfigError("Invalid provider path.")
        if path.is_symlink():
            raise ProviderConfigError("Provider directory must not be a symbolic link.")
        return path

    def list_configs(self) -> list[dict[str, str]]:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        result = []
        for directory in sorted(self.root.iterdir()):
            if not directory.is_dir() or directory.is_symlink():
                continue
            slug = directory.name
            try:
                self.validate_slug(slug)
                file_path = directory / ".env"
                if file_path.is_symlink() or not file_path.is_file():
                    continue
                values = {k: str(v) for k, v in dotenv_values(file_path).items() if v is not None}
                kind = values.get("IDP_TYPE", "").casefold()
                if kind not in _FIELDS:
                    continue
                result.append({
                    "id": slug,
                    "display_name": values.get("IDP_DISPLAY_NAME", slug),
                    "provider_type": kind,
                    "is_enabled": values.get("IDP_ENABLED", "true").casefold() == "true",
                })
            except (OSError, ProviderConfigError):
                continue
        return result

    def describe(self, slug: str) -> dict[str, Any]:
        directory = self._directory(slug)
        file_path = directory / ".env"
        if file_path.is_symlink() or not file_path.is_file():
            raise ProviderConfigError("Identity provider configuration does not exist.")
        values = {k: str(v) for k, v in dotenv_values(file_path).items() if v is not None}
        kind = values.get("IDP_TYPE", "").casefold()
        if kind not in _FIELDS:
            raise ProviderConfigError("Unsupported identity provider type.")
        secret_keys = {"AUTHENTIK_API_TOKEN", "OIDC_CLIENT_SECRET", "LDAP_BIND_PASSWORD"}
        return {
            "id": slug, "display_name": values.get("IDP_DISPLAY_NAME", slug),
            "provider_type": kind, "is_enabled": values.get("IDP_ENABLED", "true").casefold() == "true",
            "settings": {key: value for key, value in values.items() if key in _FIELDS[kind] and key not in secret_keys},
            "secret_configured": any(bool(values.get(key)) for key in secret_keys & _FIELDS[kind]),
        }

    def load(self, slug: str):
        directory = self._directory(slug)
        file_path = directory / ".env"
        if file_path.is_symlink() or not file_path.is_file():
            raise ProviderConfigError("Identity provider configuration does not exist.")
        values = {k: str(v) for k, v in dotenv_values(file_path).items() if v is not None}
        kind = values.get("IDP_TYPE", "").casefold()
        if kind not in _FIELDS:
            raise ProviderConfigError("Unsupported identity provider type.")
        provider = self._build(kind, values)
        self._configs[slug] = values
        self._providers[slug] = provider
        return provider

    def _build(self, kind: str, values: dict[str, str]):
        if kind == "authentik":
            return AuthentikAttributeProvider(
                base_url=values.get("AUTHENTIK_URL", ""),
                api_token=values.get("AUTHENTIK_API_TOKEN", ""),
                timeout=float(values.get("AUTHENTIK_TIMEOUT", "5")),
            )
        return LDAPAttributeProvider(
            url=values.get("LDAP_URL", ""),
            bind_dn=values.get("LDAP_BIND_DN", ""),
            bind_password=values.get("LDAP_BIND_PASSWORD", ""),
            base_dn=values.get("LDAP_BASE_DN", ""),
            user_filter=values.get("LDAP_USER_FILTER", "(&(objectCategory=person)(objectClass=user)(mail={email}))"),
            timeout=float(values.get("LDAP_TIMEOUT", "5")),
            ca_cert_file=values.get("LDAP_CA_CERT_FILE", ""),
        )

    def save(self, slug: str, display_name: str, kind: str, settings: dict[str, Any], enabled: bool = True):
        slug = self.validate_slug(slug)
        kind = str(kind).casefold()
        if kind not in _FIELDS:
            raise ProviderConfigError("Provider type must be authentik or ldap.")
        if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 120:
            raise ProviderConfigError("A display name of 1–120 characters is required.")
        if not isinstance(settings, dict):
            raise ProviderConfigError("Provider settings must be an object.")
        allowed = _FIELDS[kind]
        if set(settings) - allowed:
            raise ProviderConfigError("Unknown settings supplied for this provider type.")
        values = {key: str(value) for key, value in settings.items() if value is not None}
        if kind == "authentik":
            oidc_keys = ("OIDC_DISCOVERY_URL", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "OIDC_REDIRECT_URI")
            if any(values.get(key) for key in oidc_keys):
                if not all(values.get(key) for key in oidc_keys):
                    raise ProviderConfigError("OIDC discovery URL, client ID, client secret and redirect URI are all required.")
                discovery = urlsplit(values["OIDC_DISCOVERY_URL"])
                redirect = urlsplit(values["OIDC_REDIRECT_URI"])
                if (
                    discovery.scheme != "https" or not discovery.hostname
                    or discovery.username is not None or discovery.password is not None
                    or discovery.query or discovery.fragment
                ):
                    raise ProviderConfigError("OIDC discovery URL must be HTTPS without embedded credentials, query or fragment.")
                if (
                    redirect.scheme != "https" or not redirect.hostname
                    or redirect.username is not None or redirect.password is not None
                    or redirect.query or redirect.fragment
                    or redirect.path.rstrip("/") != f"/auth/{slug}"
                ):
                    raise ProviderConfigError(f"OIDC redirect URI must be HTTPS and end exactly in /auth/{slug}.")
                post_logout = values.get("OIDC_POST_LOGOUT_REDIRECT_URI", "")
                if post_logout:
                    post_logout_parts = urlsplit(post_logout)
                    if (
                        post_logout_parts.scheme != "https" or not post_logout_parts.hostname
                        or post_logout_parts.username is not None or post_logout_parts.password is not None
                        or post_logout_parts.query or post_logout_parts.fragment
                    ):
                        raise ProviderConfigError("OIDC post-logout redirect URI must be HTTPS without embedded credentials, query or fragment.")
        existing_file = self._directory(slug) / ".env"
        if existing_file.is_symlink():
            raise ProviderConfigError("Provider configuration must not be a symbolic link.")
        if existing_file.is_file():
            previous = {k: str(v) for k, v in dotenv_values(existing_file).items() if v is not None}
            secret_keys = {"AUTHENTIK_API_TOKEN", "OIDC_CLIENT_SECRET", "LDAP_BIND_PASSWORD"}
            for key in secret_keys & allowed:
                if not values.get(key) and previous.get("IDP_TYPE") == kind and previous.get(key):
                    values[key] = previous[key]
        values.update({"IDP_TYPE": kind, "IDP_DISPLAY_NAME": display_name.strip(), "IDP_ENABLED": "true" if enabled else "false"})
        # Validate all values before touching disk.
        self._build(kind, values)
        directory = self._directory(slug)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink():
            raise ProviderConfigError("Provider directory must not be a symbolic link.")
        os.chmod(directory, 0o700)
        target = directory / ".env"
        if target.is_symlink():
            raise ProviderConfigError("Provider configuration must not be a symbolic link.")
        content = "".join(f"{key}={_dotenv_quote(value)}\n" for key, value in sorted(values.items()))
        fd, temp_name = tempfile.mkstemp(prefix=".env.", dir=directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, target)
            os.chmod(target, 0o600)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        self._providers.pop(slug, None)
        self._configs.pop(slug, None)
        self.load(slug)
        return {"id": slug, "display_name": values["IDP_DISPLAY_NAME"], "provider_type": kind, "is_enabled": enabled}

    def get_config(self, slug: str) -> dict[str, str]:
        """Return raw provider settings for trusted internal authentication code only."""
        if slug not in self._configs:
            self.load(slug)
        return dict(self._configs[slug])

    def get(self, slug: str):
        if slug not in self._providers:
            self.load(slug)
        values = self._configs[slug]
        if values.get("IDP_ENABLED", "true").casefold() != "true":
            return None
        return self._providers[slug]

    async def authenticate(self, slug: str, email: str, password: str):
        """Authenticate against an enabled provider that supports password binds."""
        provider = self.get(slug)
        if provider is None:
            raise ProviderConfigError("Identity provider is disabled or unavailable.")
        authenticate = getattr(provider, "authenticate", None)
        if not callable(authenticate):
            raise ProviderConfigError("This identity provider does not support password authentication.")
        return await authenticate(email, password)

    async def test(self, slug: str, email: str = "opensigweave-provider-test@example.invalid"):
        provider = self.load(slug)
        values = self._configs[slug]
        if values.get("IDP_TYPE", "").casefold() == "authentik":
            tested = False
            discovery_url = values.get("OIDC_DISCOVERY_URL", "")
            if discovery_url:
                from identity.providers.authentik import httpx
                try:
                    async with httpx.AsyncClient(
                        verify=True,
                        timeout=httpx.Timeout(float(values.get("AUTHENTIK_TIMEOUT", "5"))),
                    ) as client:
                        response = await client.get(discovery_url)
                    if response.status_code != 200:
                        raise ProviderConfigError(f"OIDC discovery returned HTTP {response.status_code}.")
                    metadata = response.json()
                    if not isinstance(metadata, dict):
                        raise ProviderConfigError("OIDC discovery returned invalid metadata.")
                    for key in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
                        endpoint = urlsplit(str(metadata.get(key, "")))
                        if endpoint.scheme != "https" or not endpoint.hostname:
                            raise ProviderConfigError("OIDC discovery metadata contains an invalid HTTPS endpoint.")
                except ProviderConfigError:
                    raise
                except Exception as exc:
                    raise ProviderConfigError("OIDC discovery connection failed.") from exc
                tested = True
            if values.get("AUTHENTIK_URL") and values.get("AUTHENTIK_API_TOKEN"):
                await provider.get_attributes(email)
                tested = True
            if not tested:
                raise ProviderConfigError("Configure OIDC discovery or Authentik API credentials before testing.")
            return True
        # A successful LDAP query with no matching test account is still a valid connection.
        await provider.get_attributes(email)
        return True

    def delete(self, slug: str):
        directory = self._directory(slug)
        if directory.is_symlink():
            raise ProviderConfigError("Provider directory must not be a symbolic link.")
        import shutil
        if directory.exists():
            shutil.rmtree(directory)
        self._providers.pop(slug, None)
        self._configs.pop(slug, None)
