"""Shared normalization helpers for user-facing identifiers."""
import re


def slugify(value: str) -> str:
    """Convert a label into a lowercase, hyphen-separated slug."""
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
