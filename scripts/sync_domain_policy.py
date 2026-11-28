#!/usr/bin/env python3
"""On-demand or cron-based atomic sync of a mailserver-local offline policy.

Run on the Mailcow host, not as an OpenSigWeave service. Python stdlib only.
Uses a root-protected ENGINE_API_KEY file and normal TLS verification.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

FORMAT = "opensigweave-domain-policy-v1"
MAX_BYTES = 65536
VALID_SECONDS = 86400
DOMAIN = re.compile(r"(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\Z")


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON policy field")
        result[key] = value
    return result


def validate_snapshot(data: object, *, now: int | None = None) -> dict:
    if not isinstance(data, dict) or set(data) != {"format", "generated_at", "expires_at", "domains"}:
        raise ValueError("Malformed policy envelope")
    current = int(time.time()) if now is None else int(now)
    generated, expires = data["generated_at"], data["expires_at"]
    if data["format"] != FORMAT or type(generated) is not int or type(expires) is not int:
        raise ValueError("Unsupported policy format")
    if not (0 < generated <= current + 300 and current < expires <= generated + VALID_SECONDS):
        raise ValueError("Policy is expired or has an invalid validity window")
    domains = data["domains"]
    if not isinstance(domains, dict) or len(domains) > 10000:
        raise ValueError("Invalid domain policy set")
    for name, required in domains.items():
        if not isinstance(name, str) or not DOMAIN.fullmatch(name) or type(required) is not bool:
            raise ValueError("Invalid domain policy entry")
    return data


def fetch_snapshot(url: str, api_key: str) -> dict:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path != "/api/v1/mail/domain-policy"):
        raise ValueError("Use the configured OpenSigWeave HTTPS /api/v1/mail/domain-policy endpoint")
    if len(api_key) < 32 or "\r" in api_key or "\n" in api_key:
        raise ValueError("Invalid ENGINE_API_KEY")
    opener = build_opener(NoRedirects)
    request = Request(url, headers={"X-Engine-Key": api_key, "Accept": "application/json",
                                    "User-Agent": "OpenSigWeave-domain-policy-sync/1"})
    with opener.open(request, timeout=15) as response:
        if response.status != 200:
            raise ValueError("Unexpected response status")
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Domain policy is too large")
    return validate_snapshot(json.loads(raw.decode("utf-8"), object_pairs_hook=strict_object))


def atomic_install(snapshot: dict, destination: Path) -> None:
    directory = destination.parent
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Policy destination directory must already exist and must not be a symlink")
    if destination.is_symlink():
        raise ValueError("Policy destination must not be a symlink")
    if destination.exists() and not destination.is_file():
        raise ValueError("Policy destination is not a regular file")
    data = (json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
    fd, temporary = tempfile.mkstemp(prefix=".opensigweave-policy-", dir=directory)
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, destination)
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Sync a validated offline domain policy")
    parser.add_argument("--url", required=True, help="HTTPS domain-policy endpoint")
    parser.add_argument("--key-file", required=True, type=Path, help="Root-owned, 0600 ENGINE_API_KEY text file")
    parser.add_argument("--output", required=True, type=Path, help="Mailcow host's Rspamd local.d policy JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and validate without replacing the snapshot")
    args = parser.parse_args(argv)
    try:
        key_stat = args.key_file.stat()
        if not stat.S_ISREG(key_stat.st_mode) or args.key_file.is_symlink() or key_stat.st_mode & 0o077:
            raise ValueError("ENGINE_API_KEY file must be a non-symlink regular file with 0600 permissions")
        key = args.key_file.read_text(encoding="utf-8").strip()
        snapshot = fetch_snapshot(args.url, key)
        if not args.dry_run:
            atomic_install(snapshot, args.output)
        print("Validated domain policy for %d domains, valid until %s%s" % (
            len(snapshot["domains"]), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(snapshot["expires_at"])),
            " (dry run)" if args.dry_run else "",
        ))
        return 0
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        print("Domain policy sync failed; existing last-known-good file was not replaced: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
