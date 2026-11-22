"""Interactive, idempotent .env creation and conservative configuration migration."""
from __future__ import annotations

import argparse
import ipaddress
from getpass import getpass
import os
from pathlib import Path
import secrets
import sys
import tempfile
from urllib.parse import quote

from deploy_common import (
    ROOT, DeploymentError, CONFIG_KEYS, dotenv_quote, exclusive_lock,
    info, ok, parse_env, validate_config, warn,
)

ENV = ROOT / ".env"
DEFAULTS = {
    "POSTGRES_DB": "opensigweave",
    "POSTGRES_USER": "opensigweave",
    "SIGNATURE_STORAGE_PATH": "/data/signatures",
    "APP_BIND_ADDRESS": "127.0.0.1",
    "APP_PORT": "8085",
    "MANAGEMENT_API_KEY": "",
    "STALWART_HOOK_TOKEN": "",
    "LOGIN_RATE_LIMIT": "10",
    "AUTH_CALLBACK_RATE_LIMIT": "20",
    "ENGINE_RATE_LIMIT": "1800",
    "TRUSTED_PROXY_CIDRS": "",
}
# Keys without an explicit value require administrator intervention, not placeholders.
REQUIRED = ("POSTGRES_PASSWORD", "SECRET_KEY", "ENGINE_API_KEY",
            "APP_ALLOWED_HOSTS")


def prompt(label: str, default: str = "") -> str:
    answer = input(f"{label} [{default}]: " if default else f"{label}: ").strip()
    return answer or default


def create_values() -> dict[str, str]:
    from auth_config import hash_local_admin_password, is_valid_email

    hostname = prompt("Public hostname (FQDN)")
    if not hostname:
        raise DeploymentError("Public hostname is required", 4)
    bind = prompt("Bind IP (loopback is recommended for a local proxy)", "127.0.0.1")
    port = prompt("Host application port", "8085")
    email = prompt("Local administrator email", "admin@" + hostname)
    if not is_valid_email(email):
        raise DeploymentError("Invalid email address", 4)
    password = getpass("Administrator password (at least 12 characters): ")
    confirm = getpass("Confirm administrator password: ")
    if password != confirm:
        raise DeploymentError("Passwords do not match", 4)
    local_hash = hash_local_admin_password(password)
    del password, confirm
    print("Reverse proxy: terminate TLS at your trusted proxy and forward to the configured bind IP/port.")
    proxy_cidrs = prompt("Trusted immediate reverse proxy IPs/CIDRs (optional, comma-separated)", "")
    if proxy_cidrs:
        try:
            for network in proxy_cidrs.split(","):
                ipaddress.ip_network(network.strip(), strict=False)
        except ValueError as exc:
            raise DeploymentError("Invalid reverse proxy CIDR list", 4) from exc
    values = dict(DEFAULTS)
    values["TRUSTED_PROXY_CIDRS"] = proxy_cidrs
    values.update({
        "POSTGRES_PASSWORD": secrets.token_urlsafe(48),
        "SECRET_KEY": secrets.token_urlsafe(48),
        "ENGINE_API_KEY": secrets.token_urlsafe(48),
        "APP_ALLOWED_HOSTS": hostname,
        "APP_BIND_ADDRESS": bind,
        "APP_PORT": port,
        "LOCAL_ADMIN_EMAIL": email,
        "LOCAL_ADMIN_PASSWORD_HASH": local_hash,
    })
    values["DATABASE_URL"] = (
        "postgresql+psycopg://" + values["POSTGRES_USER"] + ":" +
        quote(values["POSTGRES_PASSWORD"], safe="") + "@postgres:5432/" +
        values["POSTGRES_DB"]
    )
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


def create_config() -> None:
    values = create_values()
    content = "# OpenSigWeave local deployment (never commit)\n"
    for key in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD",
                "DATABASE_URL", "SIGNATURE_STORAGE_PATH", "APP_BIND_ADDRESS",
                "APP_PORT", "SECRET_KEY", "ENGINE_API_KEY", "APP_ALLOWED_HOSTS",
                "LOCAL_ADMIN_EMAIL", "LOCAL_ADMIN_PASSWORD_HASH"):
        # Compose interpolates $ inside double quotes; scrypt contains $ separators.
        value = ("'" + values[key] + "'") if key == "LOCAL_ADMIN_PASSWORD_HASH" else dotenv_quote(values[key])
        content += f"{key}={value}\n"
    if values.get("TRUSTED_PROXY_CIDRS"):
        content += "TRUSTED_PROXY_CIDRS=" + dotenv_quote(values["TRUSTED_PROXY_CIDRS"]) + "\n"
    _atomic_create(content)
    ok("Created .env with permissions 0600; secrets were not displayed")


def missing_options(env: dict[str, str]) -> dict[str, str]:
    """Only auto-add keys with safe defaults; required secrets never guessed."""
    missing_required = [key for key in REQUIRED if not env.get(key)]
    if missing_required:
        raise DeploymentError(
            "Required values missing: " + ", ".join(missing_required) +
            ". Add them manually; the generator will not rotate or guess secrets.", 4
        )
    return {key: value for key, value in DEFAULTS.items() if key not in env}


def migrate_config(*, yes: bool = False, preview: bool = False) -> None:
    existing = parse_env(ENV)
    validate_config(existing)
    additions = missing_options(existing)
    if not additions:
        ok("Configuration is valid; no changes needed")
        return
    info("Missing optional keys: " + ", ".join(sorted(additions)))
    for key, value in sorted(additions.items()):
        print(f"  + {key}={dotenv_quote(value)}")
    if preview:
        return
    if not yes and input("Append these defaults to .env? [y/N]: ").strip().lower() != "y":
        raise DeploymentError("Configuration migration cancelled", 4)
    content = ENV.read_text(encoding="utf-8")
    content += "\n# Optional defaults appended by generate_config.sh\n"
    content += "".join(f"{key}={dotenv_quote(value)}\n" for key, value in additions.items())
    descriptor, temp = tempfile.mkstemp(prefix=".env.pending-", dir=ROOT)
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
        ok("Configuration migrated atomically; existing values preserved")
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create, check or safely migrate OpenSigWeave .env")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="Validate existing .env without changing it")
    group.add_argument("--preview", action="store_true", help="Show missing optional keys, without secrets")
    group.add_argument("--migrate", action="store_true", help="Append missing safe defaults")
    parser.add_argument("--yes", action="store_true", help="Approve optional default additions")
    args = parser.parse_args(argv)
    try:
        with exclusive_lock():
            if not ENV.exists() and not ENV.is_symlink():
                if args.check or args.preview or args.migrate:
                    raise DeploymentError(".env does not exist; run ./generate_config.sh", 4)
                create_config()
            elif args.check:
                validate_config(parse_env(ENV))
                ok("Configuration valid")
            elif args.preview:
                migrate_config(preview=True)
            elif args.migrate:
                migrate_config(yes=args.yes)
            else:
                warn(".env already exists. Use --check, --preview or --migrate; no changes made.")
        return 0
    except (DeploymentError, ValueError, OSError, EOFError, KeyboardInterrupt) as exc:
        code = exc.code if isinstance(exc, DeploymentError) else 4
        print(f"[FAIL] {exc}", file=sys.stderr)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
