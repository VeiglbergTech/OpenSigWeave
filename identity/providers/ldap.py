"""LDAP/Active Directory employee attribute provider (LDAPS only)."""
import asyncio
import logging
import re
import ssl
from urllib.parse import urlsplit

import ldap3
from ldap3.core.exceptions import LDAPBindError, LDAPException
from ldap3.utils.conv import escape_filter_chars

from identity.attributes import IdentityAttributes
from identity.providers.base import IdentityProviderUnavailable

logger = logging.getLogger(__name__)


def _first(value) -> str:
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    return "" if value is None else str(value)


def _values(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _group_name(distinguished_name: str) -> str:
    match = re.search(r"(?:^|,)\s*CN=([^,]+)", distinguished_name, re.IGNORECASE)
    return match.group(1).replace(r"\,", ",") if match else distinguished_name


class LDAPAttributeProvider:
    """Read profile attributes from LDAP using a dedicated read-only bind account.

    LDAPS and certificate validation are mandatory. The default filter targets
    Active Directory user objects by their mail attribute; deployments can
    override it while retaining the required {email} substitution.
    """

    def __init__(
        self,
        url: str,
        bind_dn: str,
        bind_password: str,
        base_dn: str,
        user_filter: str = "(&(objectCategory=person)(objectClass=user)(mail={email}))",
        timeout: float = 5.0,
        ca_cert_file: str = "",
    ):
        self.url = url.rstrip("/")
        self.bind_dn = bind_dn
        self.bind_password = bind_password
        self.base_dn = base_dn
        self.user_filter = user_filter
        self.timeout = timeout
        self.ca_cert_file = ca_cert_file or None
        parsed = urlsplit(self.url)
        if self.url and (
            parsed.scheme != "ldaps"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError("LDAP_URL must be an LDAPS URL without embedded credentials, query or fragment")
        if "{email}" not in self.user_filter:
            raise ValueError("LDAP_USER_FILTER must contain the {email} placeholder")

    async def get_attributes(self, email: str) -> IdentityAttributes:
        if not all((self.url, self.bind_dn, self.bind_password, self.base_dn)):
            raise IdentityProviderUnavailable("LDAP attribute provider is not fully configured")
        try:
            return await asyncio.to_thread(self._lookup, email.strip())
        except IdentityProviderUnavailable:
            raise
        except (LDAPException, OSError, ValueError, TypeError) as exc:
            logger.warning("LDAP attribute lookup failed: %s", type(exc).__name__)
            raise IdentityProviderUnavailable("LDAP attribute lookup failed") from exc

    async def authenticate(self, email: str, password: str) -> IdentityAttributes | None:
        """Authenticate a directory user after an exact email search.

        The service account is used only to locate the account. The submitted
        password is checked by a second LDAPS bind as the user's own DN.
        Invalid credentials and unknown/disabled accounts return None;
        infrastructure failures raise IdentityProviderUnavailable.
        """
        if (
            not all((self.url, self.bind_dn, self.bind_password, self.base_dn))
            or not isinstance(email, str)
            or not email.strip()
            or not isinstance(password, str)
            or not password
            or len(password) > 1024
        ):
            raise IdentityProviderUnavailable("LDAP authentication is not fully configured")
        try:
            return await asyncio.to_thread(self._authenticate, email.strip(), password)
        except IdentityProviderUnavailable:
            raise
        except (LDAPException, OSError, ValueError, TypeError) as exc:
            logger.warning("LDAP authentication failed: %s", type(exc).__name__)
            raise IdentityProviderUnavailable("LDAP authentication service unavailable") from exc

    def _authenticate(self, email: str, password: str) -> IdentityAttributes | None:
        parsed = urlsplit(self.url)
        server = ldap3.Server(
            parsed.hostname,
            port=parsed.port or 636,
            use_ssl=True,
            tls=ldap3.Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=self.ca_cert_file),
            get_info=ldap3.NONE,
            connect_timeout=self.timeout,
        )
        service_connection = None
        user_dn = ""
        entry_data = {}
        try:
            service_connection = ldap3.Connection(
                server,
                user=self.bind_dn,
                password=self.bind_password,
                auto_bind=True,
                receive_timeout=self.timeout,
                raise_exceptions=True,
            )
            search_filter = self.user_filter.replace("{email}", escape_filter_chars(email))
            service_connection.search(
                search_base=self.base_dn,
                search_filter=search_filter,
                search_scope=ldap3.SUBTREE,
                attributes=[
                    "mail", "givenname", "sn", "displayName", "title",
                    "telephonenumber", "memberof", "samaccountname",
                    "distinguishedname", "useraccountcontrol", "objectGUID",
                ],
                size_limit=3,
            )
            entries = list(service_connection.entries)
            if not entries:
                return None
            if len(entries) != 1:
                raise IdentityProviderUnavailable("LDAP returned ambiguous matching accounts")
            entry = entries[0]
            entry_data = {
                str(key).casefold(): value
                for key, value in entry.entry_attributes_as_dict.items()
            }
            if _first(entry_data.get("mail")).strip().casefold() != email.casefold():
                raise IdentityProviderUnavailable("LDAP result did not match the requested email")
            account_control = _first(entry_data.get("useraccountcontrol"))
            if account_control:
                try:
                    if int(account_control) & 2:
                        return None
                except ValueError as exc:
                    raise IdentityProviderUnavailable("LDAP returned invalid account status") from exc
            user_dn = str(getattr(entry, "entry_dn", "") or _first(entry_data.get("distinguishedname")))
            if not user_dn or len(user_dn) > 2048:
                raise IdentityProviderUnavailable("LDAP user entry has no valid distinguished name")
        finally:
            if service_connection is not None:
                service_connection.unbind()

        user_connection = None
        try:
            user_connection = ldap3.Connection(
                server,
                user=user_dn,
                password=password,
                auto_bind=True,
                receive_timeout=self.timeout,
                raise_exceptions=True,
            )
        except LDAPBindError:
            return None
        finally:
            if user_connection is not None:
                user_connection.unbind()

        groups = frozenset(
            name for name in (_group_name(value) for value in _values(entry_data.get("memberof"))) if name
        )
        attributes = {
            key: _values(entry_data.get(key))
            for key in ("title", "telephonenumber", "displayName", "samaccountname")
            if entry_data.get(key) is not None
        }
        claims = {
            key: _first(entry_data.get(key))
            for key in ("mail", "displayName", "samaccountname", "distinguishedname", "useraccountcontrol")
            if entry_data.get(key) is not None
        }
        return IdentityAttributes(
            first_name=_first(entry_data.get("givenname")),
            last_name=_first(entry_data.get("sn")),
            title=_first(entry_data.get("title")),
            phone=_first(entry_data.get("telephonenumber")),
            groups=groups,
            claims=claims,
            attributes=attributes,
        )

    def _lookup(self, email: str) -> IdentityAttributes:
        parsed = urlsplit(self.url)
        server = ldap3.Server(
            parsed.hostname,
            port=parsed.port or 636,
            use_ssl=True,
            tls=ldap3.Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=self.ca_cert_file),
            get_info=ldap3.NONE,
            connect_timeout=self.timeout,
        )
        connection = None
        try:
            connection = ldap3.Connection(
                server,
                user=self.bind_dn,
                password=self.bind_password,
                auto_bind=True,
                receive_timeout=self.timeout,
                raise_exceptions=True,
            )
            search_filter = self.user_filter.replace("{email}", escape_filter_chars(email))
            connection.search(
                search_base=self.base_dn,
                search_filter=search_filter,
                search_scope=ldap3.SUBTREE,
                attributes=[
                    "mail", "givenname", "sn", "displayName", "title",
                    "telephonenumber", "memberof", "samaccountname",
                    "distinguishedname", "useraccountcontrol",
                ],
                size_limit=3,
            )
            entries = list(connection.entries)
            if not entries:
                return IdentityAttributes()
            if len(entries) != 1:
                raise IdentityProviderUnavailable("LDAP returned ambiguous matching accounts")

            entry = entries[0]
            data = {str(key).casefold(): value for key, value in entry.entry_attributes_as_dict.items()}
            found_email = _first(data.get("mail")).strip().casefold()
            if found_email != email.casefold():
                raise IdentityProviderUnavailable("LDAP result did not match the requested email")
            account_control = _first(data.get("useraccountcontrol"))
            if account_control:
                try:
                    if int(account_control) & 2:
                        return IdentityAttributes()
                except ValueError as exc:
                    raise IdentityProviderUnavailable("LDAP returned invalid account status") from exc

            groups = frozenset(
                name for name in (_group_name(value) for value in _values(data.get("memberof"))) if name
            )
            attributes = {
                key: _values(data.get(key))
                for key in ("title", "telephonenumber", "displayName", "samaccountname")
                if data.get(key) is not None
            }
            claims = {
                key: _first(data.get(key))
                for key in ("mail", "displayName", "samaccountname", "distinguishedname", "useraccountcontrol")
                if data.get(key) is not None
            }
            raw_object_guid = data.get("objectguid")
            if isinstance(raw_object_guid, (bytes, bytearray)):
                stable_guid = bytes(raw_object_guid).hex()
            else:
                stable_guid = _first(raw_object_guid).strip()
            if not stable_guid:
                raise IdentityProviderUnavailable("LDAP result omitted immutable objectGUID")
            return IdentityAttributes(
                principal_id=f"ldap:{stable_guid.casefold()}",
                first_name=_first(data.get("givenname")),
                last_name=_first(data.get("sn")),
                title=_first(data.get("title")),
                phone=_first(data.get("telephonenumber")),
                groups=groups,
                claims=claims,
                attributes=attributes,
            )
        finally:
            if connection is not None:
                connection.unbind()
