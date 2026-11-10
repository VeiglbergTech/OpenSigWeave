"""Normalized identity attributes consumed by the signature core."""
from dataclasses import dataclass


@dataclass(frozen=True)
class IdentityAttributes:
    first_name: str = ""
    last_name: str = ""
    title: str = ""
    phone: str = ""
