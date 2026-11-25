"""Minimal, idempotent and atomic OpenSigWeave configuration assistant."""
from __future__ import annotations

import argparse
import base64
import ipaddress
from getpass import getpass
import os
from pathlib import Path
import re
import secrets
import socket
import sys
import tempfile
from urllib.parse import quote

from deploy_common import (
    ROOT, DeploymentError, dotenv_quote, exclusive_lock, info, ok, parse_env,
    validate_config,
)

# Shell entrypoints execute this file from scripts/, not the repository root.
# Keep top-level authentication helpers importable on a clean installation.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ENV = ROOT / ".env"
GENERATED_PASSWORD_FILE = ROOT / ".admin-recovery-credentials"
DEFAULTS = {
    "POSTGRES_DB": "opensigweave",
    "POSTGRES_USER": "opensigweave",
    "SIGNATURE_STORAGE_PATH": "/data/signatures",
    "APP_BIND_ADDRESS": "0.0.0.0",
    "APP_PORT": "8085",
    "MANAGEMENT_API_KEY": "",
    "STALWART_HOOK_TOKEN": "",
    "LOGIN_RATE_LIMIT": "10",
    "AUTH_CALLBACK_RATE_LIMIT": "20",
    "ENGINE_RATE_LIMIT": "1800",
    "TRUSTED_PROXY_CIDRS": "",
}
REQUIRED = ("POSTGRES_PASSWORD", "SECRET_KEY", "ENGINE_API_KEY", "APP_ALLOWED_HOSTS")
GENERATED_KEYS = ("BACKUP_LOCATIONS_KEY",)


def prompt(label: str, default: str = "") -> str:
    """Optional prompt: safe values work for local/headless defaults."""
    if not sys.stdin.isatty():
        return default
    answer = input(f"{label} [{default}]: " if default else f"{label}: ").strip()
    return answer or default


def _allowed_hosts(raw: str) -> str:
    """Normalize a comma-separated allowlist; never accept empty/wildcard hosts."""
    hosts = [host.strip().lower() for host in raw.split(",")]
    if any(not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", host) for host in hosts):
        raise DeploymentError(
            "Allowed hosts must be comma-separated hostnames/IPs without schemes, ports or wildcards", 4
        )
    return ",".join(dict.fromkeys(hosts))


def _trusted_proxies(raw: str) -> str:
    """Accept only IP addresses or CIDRs, not hostnames or trust-everyone ranges."""
    if not raw.strip():
        return ""
    normalized = []
    for item in raw.split(","):
        candidate = item.strip()
        try:
            network = ipaddress.ip_network(candidate, strict=False)
        except ValueError as exc:
            raise DeploymentError(
                "Trusted proxies must be comma-separated IP addresses/CIDRs (not DNS names)", 4
            ) from exc
        if network.prefixlen == 0:
            raise DeploymentError(
                "Refusing to trust every IP address as a reverse proxy (no /0 networks)", 4
            )
        value = str(network)
        if value not in normalized:
            normalized.append(value)
    return ",".join(normalized)


def _hostname_default() -> str:
    value = socket.getfqdn().lower().strip(".")
    if (value and "." in value and
            re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", value)):
        return value
    return "localhost"


def _available(bind: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET6 if ":" in bind else socket.AF_INET,
                           socket.SOCK_STREAM) as probe:
            probe.bind((bind, port))
            return True
    except OSError:
        return False


def _free_port(bind: str) -> int:
    for port in range(8085, 8106):
        if _available(bind, port):
            return port
    raise DeploymentError("No free application port in 8085-8105; free a port before installing", 4)


def _fernet_key() -> str:
    """32 random bytes, Fernet's URL-safe base64 encoding; no host dependency."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


def create_values(*, hostname: str | None = None, admin_email: str | None = None,
                  allowed_hosts: str | None = None, trusted_proxy_cidrs: str | None = None,
                  generate_admin_password: bool = False) -> dict[str, str]:
    from auth_config import hash_local_admin_password, is_valid_email

    host = hostname or prompt("Hostname (localhost for local-only use)", _hostname_default())
    host = host.lower().strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", host):
        raise DeploymentError("Invalid application hostname", 4)
    email_default = "admin@" + (host if "." in host else "localhost.localdomain")
    email = admin_email or prompt("Administrator email", email_default)
    if not is_valid_email(email):
        raise DeploymentError("Invalid administrator email", 4)
    allowlist = _allowed_hosts(
        allowed_hosts if allowed_hosts is not None else
        prompt("Allowed portal hostnames (comma-separated, no https://)", host)
    )
    proxies = _trusted_proxies(
        trusted_proxy_cidrs if trusted_proxy_cidrs is not None else
        prompt("Trusted proxy IPs/CIDRs (comma-separated; blank if none)", "")
    )
    bind = prompt(
        "Application bind IP (0.0.0.0 = all IPv4 interfaces)",
        DEFAULTS["APP_BIND_ADDRESS"],
    ).strip()
    try:
        ipaddress.ip_address(bind)
    except ValueError as exc:
        raise DeploymentError("Bind address must be one valid IPv4 or IPv6 address", 4) from exc
    if generate_admin_password:
        password = secrets.token_urlsafe(32)
    else:
        if not sys.stdin.isatty():
            raise DeploymentError(
                "An interactive terminal is required for the administrator password; "
                "alternatively use --generate-admin-password", 4
            )
        password = getpass("Administrator password (at least 12 characters): ")
        confirm = getpass("Confirm administrator password: ")
        if password != confirm:
            raise DeploymentError("Administrator passwords do not match", 4)
        del confirm
    try:
        local_hash = hash_local_admin_password(password)
    except ValueError as exc:
        raise DeploymentError(str(exc), 4) from exc
    values = dict(DEFAULTS)
    values.update({
        "POSTGRES_PASSWORD": secrets.token_urlsafe(48),
        "SECRET_KEY": secrets.token_urlsafe(48),
        "ENGINE_API_KEY": secrets.token_urlsafe(48),
        "BACKUP_LOCATIONS_KEY": _fernet_key(),
        "APP_ALLOWED_HOSTS": allowlist,
        "TRUSTED_PROXY_CIDRS": proxies,
        "APP_BIND_ADDRESS": bind,
        "APP_PORT": str(_free_port(bind)),
        "LOCAL_ADMIN_EMAIL": email,
        "LOCAL_ADMIN_PASSWORD_HASH": local_hash,
    })
    values["DATABASE_URL"] = (
        "postgresql+psycopg://" + values["POSTGRES_USER"] + ":" +
        quote(values["POSTGRES_PASSWORD"], safe="") + "@postgres:5432/" +
        values["POSTGRES_DB"]
    )
    if generate_admin_password:
        values["_GENERATED_ADMIN_PASSWORD"] = password
    del password
    validate_config(values)
    return values


def _atomic_create(content: str) -> None:
    if ENV.exists() or ENV.is_symlink():
        raise DeploymentError(".env already exists; refusing to overwrite it", 4)
    fd = os.open(ENV, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        ENV.unlink(missing_ok=True)
        raise


def _save_generated_password(password: str) -> None:
    fd = os.open(GENERATED_PASSWORD_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("OpenSigWeave initial local administrator password\n")
            stream.write(password + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        GENERATED_PASSWORD_FILE.unlink(missing_ok=True)
        raise


def create_config(*, hostname: str | None = None, admin_email: str | None = None,
                  allowed_hosts: str | None = None, trusted_proxy_cidrs: str | None = None,
                  generate_admin_password: bool = False) -> None:
    values = create_values(hostname=hostname, admin_email=admin_email,
                           allowed_hosts=allowed_hosts, trusted_proxy_cidrs=trusted_proxy_cidrs,
                           generate_admin_password=generate_admin_password)
    content = "# OpenSigWeave protected local configuration (never commit)\n"
    for key in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD",
                "DATABASE_URL", "SIGNATURE_STORAGE_PATH", "APP_BIND_ADDRESS",
                "APP_PORT", "SECRET_KEY", "ENGINE_API_KEY", "BACKUP_LOCATIONS_KEY",
                "APP_ALLOWED_HOSTS", "TRUSTED_PROXY_CIDRS",
                "LOCAL_ADMIN_EMAIL", "LOCAL_ADMIN_PASSWORD_HASH"):
        # Compose interpolates $ in double quotes; preserve the literal scrypt separators.
        value = ("'" + values[key] + "'") if key == "LOCAL_ADMIN_PASSWORD_HASH" else dotenv_quote(values[key])
        content += f"{key}={value}\n"
    if generate_admin_password:
        _save_generated_password(values["_GENERATED_ADMIN_PASSWORD"])
    try:
        _atomic_create(content)
    except BaseException:
        if generate_admin_password:
            GENERATED_PASSWORD_FILE.unlink(missing_ok=True)
        raise
    ok("Created .env with mode 0600 and independent cryptographic secrets")
    if values["APP_BIND_ADDRESS"] in ("0.0.0.0", "::"):
        info("WARNING: Application binds on all interfaces; restrict its port with a firewall and use an HTTPS reverse proxy.")
    if generate_admin_password:
        info(f"Initial administrator password stored in {GENERATED_PASSWORD_FILE} (0600); move it offline")


def missing_options(env: dict[str, str]) -> dict[str, str]:
    """Never invent a missing database password or rotate an existing secret."""
    missing_required = [key for key in REQUIRED if not env.get(key)]
    if missing_required:
        raise DeploymentError(
            "Required existing credentials are missing: " + ", ".join(missing_required) +
            ". Restore them from secure records; generating replacements could break existing data.", 4
        )
    additions = {key: value for key, value in DEFAULTS.items() if key not in env}
    # Do not silently broaden the network exposure of legacy installations
    # which previously defaulted to loopback when APP_BIND_ADDRESS was missing.
    # The new 0.0.0.0 default applies to NEW installations only.
    if "APP_BIND_ADDRESS" not in env:
        additions["APP_BIND_ADDRESS"] = "127.0.0.1"
    if not env.get("BACKUP_LOCATIONS_KEY"):
        additions["BACKUP_LOCATIONS_KEY"] = _fernet_key()
    return additions


def migrate_config(*, yes: bool = True, preview: bool = False) -> None:
    existing = parse_env(ENV)
    validate_config(existing)
    additions = missing_options(existing)
    if not additions:
        ok("Configuration valid; existing secrets preserved")
        return
    info("Safe configuration additions: " + ", ".join(sorted(additions)))
    if preview:
        for key, value in sorted(additions.items()):
            print(f"  + {key}=" + ("<generated securely>" if key in GENERATED_KEYS else dotenv_quote(value)))
        return
    content = ENV.read_text(encoding="utf-8")
    appended = []
    for key, value in sorted(additions.items()):
        if key in existing:
            # A legacy .env.example often contained BACKUP_LOCATIONS_KEY="".
            # Replace the empty assignment in place, never append a duplicate
            # dotenv key (which Compose/parse_env would reject).
            pattern = r"(?m)^[ \\t]*(?:export[ \\t]+)?" + re.escape(key) + r"[ \\t]*=.*$"
            content, count = re.subn(pattern, f"{key}={dotenv_quote(value)}", content)
            if count != 1:
                raise DeploymentError("Cannot safely replace empty setting: " + key, 4)
        else:
            appended.append(f"{key}={dotenv_quote(value)}\n")
    if appended:
        content += "\n# Idempotent safe additions from generate_config.sh\n"
        content += "".join(appended)
    descriptor, temp = tempfile.mkstemp(prefix=".env.pending-", dir=ENV.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        validate_config(parse_env(Path(temp)))
        if ENV.is_symlink():
            raise DeploymentError("Refusing to replace a symlinked .env", 4)
        os.replace(temp, ENV)
        ok("Configuration extended atomically; previous credentials preserved")
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create, check or safely extend OpenSigWeave .env")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="Read-only validation")
    group.add_argument("--preview", action="store_true", help="Preview safe additions without secrets")
    group.add_argument("--migrate", action="store_true", help="Idempotently append safe missing settings")
    parser.add_argument("--yes", action="store_true", help="Compatibility option; safe migration is automatic")
    parser.add_argument("--hostname", help="Use an explicit public hostname on first install")
    parser.add_argument("--admin-email", help="Use an explicit administrator email on first install")
    parser.add_argument("--allowed-hosts", help="Comma-separated portal hostnames (no wildcards or URL schemes)")
    parser.add_argument("--trusted-proxy-cidrs", help="Comma-separated trusted reverse proxy IPs or CIDRs; blank means none")
    parser.add_argument("--generate-admin-password", action="store_true",
                        help="Create a protected 0600 initial password file instead of prompting")
    args = parser.parse_args(argv)
    try:
        with exclusive_lock():
            if not ENV.exists() and not ENV.is_symlink():
                if args.check or args.preview or args.migrate:
                    raise DeploymentError(".env does not exist; run ./generate_config.sh", 4)
                create_config(hostname=args.hostname, admin_email=args.admin_email,
                              allowed_hosts=args.allowed_hosts,
                              trusted_proxy_cidrs=args.trusted_proxy_cidrs,
                              generate_admin_password=args.generate_admin_password)
            elif args.check:
                env = parse_env(ENV)
                validate_config(env)
                ok("Configuration valid (read-only)")
            elif args.preview:
                migrate_config(preview=True)
            else:
                migrate_config()
        return 0
    except (DeploymentError, ValueError, OSError, EOFError, KeyboardInterrupt) as exc:
        code = exc.code if isinstance(exc, DeploymentError) else 4
        print(f"[FAIL] {exc}", file=sys.stderr)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
