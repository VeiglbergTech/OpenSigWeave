"""Mailserver-local domain failure policy snapshot.

The snapshot is only trustworthy when downloaded over authenticated TLS and
atomically installed by the administrator or the bundled sync helper. A local
domain exemption is never inferred from unknown/unconfigured domain names.
"""
from __future__ import annotations

import re
import time

FORMAT = "opensigweave-domain-policy-v1"
VALID_SECONDS = 24 * 60 * 60
_DOMAIN = re.compile(r"(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\Z")


def build_snapshot(rows, *, now: int | None = None) -> dict:
    issued = int(time.time()) if now is None else int(now)
    domains: dict[str, bool] = {}
    for item in rows:
        name = str(item.domain_name or "").strip().casefold().rstrip(".")
        if not _DOMAIN.fullmatch(name):
            raise ValueError("Invalid domain in policy source")
        if name in domains:
            raise ValueError("Duplicate domain in policy source")
        if item.is_active:
            domains[name] = bool(item.signature_required)
    return {
        "format": FORMAT,
        "generated_at": issued,
        "expires_at": issued + VALID_SECONDS,
        "domains": dict(sorted(domains.items())),
    }
