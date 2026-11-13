"""Normalized identity attributes consumed by the signature core."""
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class IdentityAttributes:
    principal_id: str = ""
    first_name: str = ""
    last_name: str = ""
    title: str = ""
    phone: str = ""
    groups: frozenset[str] = frozenset()
    claims: Mapping[str, Any] = field(default_factory=dict)
    attributes: Mapping[str, Any] = field(default_factory=dict)
