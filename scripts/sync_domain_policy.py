#!/usr/bin/env python3
"""Authenticated, atomic mailserver-local domain policy synchronization.

Install on the Mailcow HOST under a root-managed timer/cron (no daemon or
Docker socket). The policy digest is an identifier, not source authentication:
TLS, the Engine key and root-owned local files are the trust boundary.
"""
from __future__ import annotations

import argparse
import hashlib
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

FORMAT = "opensigweave-domain-policy-v2"
LEGACY_FORMAT = "opensigweave-domain-policy-v1"
MAX_BYTES = 65536
LEGACY_VALID_SECONDS = 86400
DOMAIN = re.compile(r"(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\Z")
VERSION = re.compile(r"[a-f0-9]{64}\Z")


class PendingPolicyError(ValueError):
    """Expected policy version has not yet been confirmed by the mailserver."""


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


def policy_version(domains: dict) -> str:
    data = json.dumps(domains, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(data.encode("ascii")).hexdigest()


def validate_snapshot(data: object, *, now: int | None = None, fresh: bool = False,
                      allow_legacy: bool = False) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Malformed policy envelope")
    current = int(time.time()) if now is None else int(now)
    fmt = data.get("format")
    if fmt == FORMAT:
        if set(data) != {"format", "generated_at", "policy_version", "domains"}:
            raise ValueError("Malformed v2 policy envelope")
    elif fmt == LEGACY_FORMAT and allow_legacy:
        if set(data) != {"format", "generated_at", "expires_at", "domains"}:
            raise ValueError("Malformed legacy policy envelope")
    else:
        raise ValueError("Unsupported policy format")
    generated = data["generated_at"]
    if type(generated) is not int or generated <= 0 or generated > current + 300:
        raise ValueError("Invalid policy generation timestamp")
    if fresh and current - generated > 300:
        raise ValueError("Network policy response is stale")
    domains = data["domains"]
    if not isinstance(domains, dict) or len(domains) > 10000:
        raise ValueError("Invalid domain policy set")
    for name, required in domains.items():
        if not isinstance(name, str) or not DOMAIN.fullmatch(name) or type(required) is not bool:
            raise ValueError("Invalid domain policy entry")
    if fmt == FORMAT:
        if not isinstance(data["policy_version"], str) or not VERSION.fullmatch(data["policy_version"]):
            raise ValueError("Invalid policy version")
        if data["policy_version"] != policy_version(domains):
            raise ValueError("Policy version/content mismatch")
    else:
        expires = data["expires_at"]
        if (type(expires) is not int or not
                (generated < expires <= generated + LEGACY_VALID_SECONDS and current < expires)):
            raise ValueError("Expired or invalid legacy snapshot")
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
                                    "Cache-Control": "no-cache",
                                    "User-Agent": "OpenSigWeave-domain-policy-sync/2"})
    with opener.open(request, timeout=15) as response:
        if response.status != 200:
            raise ValueError("Unexpected response status")
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Domain policy is too large")
    snapshot = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_object)
    return validate_snapshot(snapshot, fresh=True)


def _safe_destination(destination: Path) -> None:
    if not destination.is_absolute() or ".." in destination.parts:
        raise ValueError("Policy destination must be an absolute, normalized path")
    directory = destination.parent
    try:
        info = directory.lstat()
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise ValueError("Policy destination directory must already exist") from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
        raise ValueError("Policy directory must exist and deny group/other writes")
    if os.geteuid() == 0 and info.st_uid != 0:
        raise ValueError("Policy directory must be root-owned")
    try:
        existing = destination.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(existing.st_mode) or existing.st_mode & 0o022:
        raise ValueError("Policy destination must be a non-symlink, protected regular file")
    if os.geteuid() == 0 and existing.st_uid != 0:
        raise ValueError("Policy destination must be root-owned")


def _encode(snapshot: dict) -> bytes:
    return (json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def installed_snapshot(destination: Path, *, now: int | None = None) -> dict | None:
    _safe_destination(destination)
    if not destination.exists():
        return None
    with destination.open("rb") as file:
        raw = file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Installed policy exceeds maximum size")
    return validate_snapshot(json.loads(raw.decode("utf-8"), object_pairs_hook=strict_object),
                             now=now, allow_legacy=True)


def atomic_install(snapshot: dict, destination: Path, *, now: int | None = None) -> bool:
    """Install verified v2 policy; return False for an already installed version."""
    validate_snapshot(snapshot, now=now)
    _safe_destination(destination)
    try:
        previous = installed_snapshot(destination, now=now)
    except (ValueError, UnicodeError, json.JSONDecodeError):
        previous = None  # Valid authenticated data can repair a corrupt local snapshot.
    if previous and previous["format"] == FORMAT and (
            previous["policy_version"] == snapshot["policy_version"]):
        return False
    data = _encode(snapshot)
    if len(data) > MAX_BYTES:
        raise ValueError("Serialized snapshot exceeds maximum size")
    directory = destination.parent
    fd, temporary = tempfile.mkstemp(prefix=".opensigweave-policy-", dir=directory)
    try:
        os.fchmod(fd, 0o644)  # Rspamd worker must be able to read, never write.
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
    return True


def _read_key(path: Path) -> str:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("ENGINE_API_KEY must be in a protected non-symlink regular file (0600)")
    if os.geteuid() == 0 and info.st_uid != 0:
        raise ValueError("ENGINE_API_KEY file must be root-owned")
    return path.read_text(encoding="utf-8").strip()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Sync and acknowledge a protected offline domain policy")
    parser.add_argument("--url", help="HTTPS domain-policy endpoint")
    parser.add_argument("--key-file", type=Path, help="Root-owned, 0600 ENGINE_API_KEY file")
    parser.add_argument("--output", required=True, type=Path, help="Mailcow host Rspamd local.d JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and validate without installing")
    parser.add_argument("--status", action="store_true", help="Show last installed trusted policy version offline")
    parser.add_argument("--expect-version", help="Require a specific 64-character desired policy version")
    args = parser.parse_args(argv)
    try:
        if args.expect_version and not VERSION.fullmatch(args.expect_version):
            raise ValueError("Invalid --expect-version fingerprint")
        if args.status:
            if args.url or args.key_file or args.dry_run:
                raise ValueError("--status only accepts --output and optional --expect-version")
            saved = installed_snapshot(args.output)
            if not saved:
                raise ValueError("No verified mailserver-local policy installed")
            version = saved.get("policy_version", "legacy-v1")
            print("INSTALLED policy_version=%s generated_at=%s domains=%d format=%s" %
                  (version, saved["generated_at"], len(saved["domains"]), saved["format"]))
            if args.expect_version and version != args.expect_version:
                raise PendingPolicyError("WARNING: desired policy version is NOT installed on this mailserver")
            return 0
        if not args.url or not args.key_file:
            raise ValueError("--url and --key-file are required for synchronization")
        if not args.dry_run and os.geteuid() != 0:
            raise ValueError("Installation requires root privileges; use --dry-run to validate")
        key = _read_key(args.key_file)
        snapshot = fetch_snapshot(args.url, key)
        version = snapshot["policy_version"]
        if args.expect_version and version != args.expect_version:
            raise PendingPolicyError("WARNING: desired policy version is NOT served by OpenSigWeave")
        if args.dry_run:
            state = "dry-run"
        else:
            state = "installed" if atomic_install(snapshot, args.output) else "unchanged"
        print("ACK policy_version=%s domains=%d state=%s checked_at=%s" % (
            version, len(snapshot["domains"]), state,
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        return 0
    except PendingPolicyError as exc:
        print(str(exc) + "; existing local policy retained", file=sys.stderr)
        return 3
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        print("Domain policy sync failed; existing last-known-good policy retained: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
