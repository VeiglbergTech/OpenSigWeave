"""Provider contract used by the signature application core."""
from typing import Protocol

from identity.attributes import IdentityAttributes


class IdentityProviderUnavailable(RuntimeError):
    """Raised when a fresh identity lookup cannot be trusted."""


class AttributeProvider(Protocol):
    async def get_attributes(self, email: str) -> IdentityAttributes:
        """Return normalized profile attributes for an email address.

        Raise IdentityProviderUnavailable when a fresh lookup cannot be trusted.
        """
