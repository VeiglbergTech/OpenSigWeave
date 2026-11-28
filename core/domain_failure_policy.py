"""Versioned mailserver-local domain policy exports.

Version 2 contains a stable SHA-256 digest of canonical domain decisions.
An authenticated, protected mailserver snapshot does not expire merely because
OpenSigWeave cannot be reached. A content hash is NOT a digital signature:
the mailserver must obtain the policy over authenticated HTTPS and protect it
against modification by untrusted local users.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

FORMAT = "opensigweave-domain-policy-v2"
LEGACY_FORMAT = "opensigweave-domain-policy-v1"
_DOMAIN = re.compile(r"(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\Z")


def policy_version(domains: dict[str, bool]) -> str:
    """Stable fingerprint; unaffected by polls that change generated_at."""
    canonical = json.dumps(domains, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


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
    if len(domains) > 10000:
        raise ValueError("Too many domain policy entries")
    domains = dict(sorted(domains.items()))
    return {
        "format": FORMAT,
        "generated_at": issued,
        "policy_version": policy_version(domains),
        "domains": domains,
    }
