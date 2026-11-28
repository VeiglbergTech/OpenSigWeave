"""Filesystem-backed, reloadable identity-provider configuration registry.

Each provider owns one private .idp/<slug>/.env file. Secrets never enter
PostgreSQL or API responses. OIDC authentication remains separate.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import os
import re
import secrets
import stat
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
    "oidc": {"OIDC_DISCOVERY_URL", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "OIDC_SCOPES", "OIDC_REDIRECT_URI", "OIDC_POST_LOGOUT_REDIRECT_URI"},
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
        # Do not resolve an untrusted symlink: every path component is opened
        # with O_NOFOLLOW below, anchored at an already-open directory.
        self.root = Path(os.path.abspath(root))
        if self.root == Path("/"):
            raise ProviderConfigError("Identity registry root must not be /.")
        self._providers: dict[str, Any] = {}
        self._configs: dict[str, dict[str, str]] = {}

    @staticmethod
    def validate_slug(slug: str) -> str:
        if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
            raise ProviderConfigError("Provider name must be a lowercase slug (a-z, 0-9, hyphens).")
        return slug

    @contextmanager
    def _root_fd(self):
        """Pin each directory component; never follow links, even in ancestors."""
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open("/", flags)
        try:
            components = self.root.parts[1:]
            for index, component in enumerate(components):
                if index == len(components) - 1:
                    try:
                        os.mkdir(component, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                child_fd = os.open(component, flags, dir_fd=fd)
                os.close(fd)
                fd = child_fd
            current = os.fstat(fd)
            if current.st_uid != os.geteuid() or not stat.S_ISDIR(current.st_mode):
                raise ProviderConfigError("Unsafe identity registry root ownership.")
            os.fchmod(fd, 0o700)
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def _provider_fd(self, root_fd: int, slug: str, *, create: bool = False):
        slug = self.validate_slug(slug)
        if create:
            try:
                os.mkdir(slug, mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
        fd = os.open(slug, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                     dir_fd=root_fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
                raise ProviderConfigError("Unsafe provider directory ownership.")
            os.fchmod(fd, 0o700)
            yield fd
        finally:
            os.close(fd)

    def _read_values(self, slug: str, *, missing_ok: bool = False) -> dict[str, str]:
        self.validate_slug(slug)
        try:
            with self._root_fd() as root_fd:
                with self._provider_fd(root_fd, slug) as provider_fd:
                    fd = os.open(".env", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 dir_fd=provider_fd)
                    with os.fdopen(fd, "r", encoding="utf-8") as stream:
                        info = os.fstat(stream.fileno())
                        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                                or info.st_mode & 0o077 or info.st_nlink != 1
                                or info.st_size > 128 * 1024):
                            raise ProviderConfigError("Unsafe provider configuration file.")
                        return {k: str(v) for k, v in dotenv_values(stream=stream).items()
                                if v is not None}
        except FileNotFoundError as exc:
            if missing_ok:
                return {}
            raise ProviderConfigError("Identity provider configuration does not exist.") from exc
        except (OSError, UnicodeError) as exc:
            raise ProviderConfigError("Cannot safely read identity provider configuration.") from exc

    def _write_values(self, slug: str, values: dict[str, str]) -> None:
        self.validate_slug(slug)
        content = "".join(f"{key}={_dotenv_quote(value)}\n"
                          for key, value in sorted(values.items()))
        with self._root_fd() as root_fd:
            with self._provider_fd(root_fd, slug, create=True) as provider_fd:
                # Reject an unsafe pre-existing .env before any replacement.
                self._read_values(slug, missing_ok=True)
                temporary = ".env." + secrets.token_hex(16)
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=provider_fd)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as stream:
                        os.fchmod(stream.fileno(), 0o600)
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, ".env", src_dir_fd=provider_fd,
                               dst_dir_fd=provider_fd)
                    os.fsync(provider_fd)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=provider_fd)
                    except FileNotFoundError:
                        pass

    def list_configs(self) -> list[dict[str, str]]:
        result = []
        with self._root_fd() as root_fd:
            for slug in sorted(os.listdir(root_fd)):
                try:
                    values = self._read_values(slug)
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
        values = self._read_values(slug)
        kind = values.get("IDP_TYPE", "").casefold()
        if kind not in _FIELDS:
            raise ProviderConfigError("Unsupported identity provider type.")
        secret_keys = {"AUTHENTIK_API_TOKEN", "OIDC_CLIENT_SECRET", "LDAP_BIND_PASSWORD"}
        return {
            "id": slug, "display_name": values.get("IDP_DISPLAY_NAME", slug),
            "provider_type": kind, "is_enabled": values.get("IDP_ENABLED", "true").casefold() == "true",
            "settings": {key: value for key, value in values.items() if key in _FIELDS[kind] and key not in secret_keys},
            "secret_configured": any(bool(values.get(key)) for key in secret_keys & _FIELDS[kind]),
            # Keep the existing generic credential flag, but distinguish an
            # Authentik API token from an OIDC client secret for login readiness.
            "oidc_secret_configured": kind in {"authentik", "oidc"} and bool(values.get("OIDC_CLIENT_SECRET")),
            "authentik_api_secret_configured": kind == "authentik" and bool(values.get("AUTHENTIK_API_TOKEN")),
        }

    def load(self, slug: str):
        values = self._read_values(slug)
        kind = values.get("IDP_TYPE", "").casefold()
        if kind not in _FIELDS:
            raise ProviderConfigError("Unsupported identity provider type.")
        provider = self._build(kind, values)
        self._configs[slug] = values
        self._providers[slug] = provider
        return provider

    def _build(self, kind: str, values: dict[str, str]):
        if kind == "oidc":
            # Generic OIDC profile claims are cached from the verified login callback.
            return None
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
            raise ProviderConfigError("Provider type must be authentik, oidc or ldap.")
        if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 120:
            raise ProviderConfigError("A display name of 1–120 characters is required.")
        if not isinstance(settings, dict):
            raise ProviderConfigError("Provider settings must be an object.")
        allowed = _FIELDS[kind]
        if set(settings) - allowed:
            raise ProviderConfigError("Unknown settings supplied for this provider type.")
        values = {key: str(value) for key, value in settings.items() if value is not None}
        # An empty secret in the admin form means keep the existing credential.
        # Restore it BEFORE OIDC's required-fields validation, not afterwards.
        previous = self._read_values(slug, missing_ok=True)
        secret_keys = {"AUTHENTIK_API_TOKEN", "OIDC_CLIENT_SECRET", "LDAP_BIND_PASSWORD"}
        for key in secret_keys & allowed:
            if not values.get(key) and previous.get("IDP_TYPE") == kind and previous.get(key):
                values[key] = previous[key]
        if kind in {"authentik", "oidc"}:
            oidc_keys = ("OIDC_DISCOVERY_URL", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "OIDC_REDIRECT_URI")
            if kind == "oidc" or any(values.get(key) for key in oidc_keys):
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
        values.update({"IDP_TYPE": kind, "IDP_DISPLAY_NAME": display_name.strip(), "IDP_ENABLED": "true" if enabled else "false"})
        # Validate all values before touching disk.
        self._build(kind, values)
        self._write_values(slug, values)
        self._providers.pop(slug, None)
        self._configs.pop(slug, None)
        self.load(slug)
        return {"id": slug, "display_name": values["IDP_DISPLAY_NAME"], "provider_type": kind, "is_enabled": enabled}

    def get_config(self, slug: str) -> dict[str, str]:
        """Return raw provider settings for trusted internal authentication code only."""
        self.validate_slug(slug)
        if slug not in self._configs:
            self.load(slug)
        return dict(self._configs[slug])

    def get(self, slug: str):
        self.validate_slug(slug)
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
        if values.get("IDP_TYPE", "").casefold() in {"authentik", "oidc"}:
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
            if values.get("IDP_TYPE", "").casefold() == "authentik" and values.get("AUTHENTIK_URL") and values.get("AUTHENTIK_API_TOKEN"):
                await provider.get_attributes(email)
                tested = True
            if not tested:
                raise ProviderConfigError("Configure OIDC discovery or Authentik API credentials before testing.")
            return True
        # A successful LDAP query with no matching test account is still a valid connection.
        await provider.get_attributes(email)
        return True

    def delete(self, slug: str):
        slug = self.validate_slug(slug)
        with self._root_fd() as root_fd:
            try:
                with self._provider_fd(root_fd, slug) as provider_fd:
                    # Never recursively remove attacker-provided names or follow links.
                    if set(os.listdir(provider_fd)) - {".env"}:
                        raise ProviderConfigError("Unexpected files in provider directory.")
                    if ".env" in os.listdir(provider_fd):
                        self._read_values(slug)
                        os.unlink(".env", dir_fd=provider_fd)
                    os.rmdir(slug, dir_fd=root_fd)
            except FileNotFoundError:
                pass
        self._providers.pop(slug, None)
        self._configs.pop(slug, None)
