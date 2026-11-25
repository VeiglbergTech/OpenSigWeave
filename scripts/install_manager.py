"""Low-touch first install, healthy-install detection and safe partial resume."""
from __future__ import annotations

import argparse
import os
import socket
import sys

from deploy_common import (
    ROOT, DeploymentError, CHANNELS, compose, current_channel, current_sha,
    exclusive_lock, healthcheck, info, ok, parse_env, pristine, service_state,
    system_checks, validate_config, warn, verify_release_image,
)
from config_manager import ENV, create_config, migrate_config, _available


def port_available(bind: str, port: int) -> bool:
    # Share the safe loopback-only wildcard probe with initial configuration.
    return _available(bind, port)


def perform_install(channel: str | None, *, hostname: str | None = None,
                    admin_email: str | None = None,
                    allowed_hosts: str | None = None, trusted_proxy_cidrs: str | None = None,
                    generate_admin_password: bool = False) -> None:
    with exclusive_lock():
        pristine()
        selected = current_channel()
        chosen = channel or selected
        if chosen not in CHANNELS or chosen != selected:
            raise DeploymentError(
                "Requested channel differs from the checkout; use git clone --branch "
                f"{chosen} https://github.com/VeiglbergTech/OpenSigWeave.git", 4
            )
        if selected != "main":
            warn(f"{selected} is not a production-stable release channel")
        system_checks()
        if ENV.is_symlink():
            raise DeploymentError("Refusing symlinked .env", 4)
        if ENV.exists():
            env = parse_env(ENV)
            validate_config(env)
            migrate_config()
            env = parse_env(ENV)
            try:
                healthcheck(env)
            except DeploymentError:
                info("Existing configuration detected; inspecting/resuming services without replacing secrets")
            else:
                ok("OpenSigWeave is already installed and healthy; no changes needed")
                return
        else:
            create_config(hostname=hostname, admin_email=admin_email,
                          allowed_hosts=allowed_hosts, trusted_proxy_cidrs=trusted_proxy_cidrs,
                          generate_admin_password=generate_admin_password)
            env = parse_env(ENV)
        validate_config(env)
        os.chmod(ENV, 0o600)
        bind = env.get("APP_BIND_ADDRESS", "0.0.0.0")  # nosec B104 - configured listening address
        if bind in ("0.0.0.0", "::"):  # nosec B104 - warns about wide host binding
            warn("Application binds to all interfaces: restrict the port in the host firewall and use an HTTPS reverse proxy.")
        port = int(env.get("APP_PORT", "8085"))
        if not port_available(bind, port):
            existing = compose("ps", "-q", "opensigweave")
            if not existing:
                raise DeploymentError(
                    f"Application port {bind}:{port} is occupied by another process; "
                    "choose a free port in the protected .env", 4
                )
            info("Application port is already bound by the existing Compose project")
        info("Validating Docker Compose without deleting any named volumes")
        compose("config", "--quiet")
        info("Starting PostgreSQL and checking database readiness")
        compose("up", "-d", "--wait", "--wait-timeout", "180", "postgres", timeout=360)
        service_state("postgres")
        if selected == "main":
            info("Pulling published GHCR image; no local application build")
            compose("pull", "opensigweave", timeout=1800)
            verify_release_image(env, current_sha())
            compose("up", "-d", "--no-build", "--wait", "--wait-timeout",
                    "240", "opensigweave", timeout=1800)
        else:
            info("Building unreleased dev/beta code with explicit build overlay")
            compose("up", "-d", "--build", "--wait", "--wait-timeout", "240",
                    "opensigweave", timeout=1800)
        healthcheck(env)
        ok("Installation successful")
        print(f"Channel: {selected} | Commit: {current_sha()}")
        print(f"Directory: {ROOT}")
        host = env["APP_ALLOWED_HOSTS"].split(",")[0]
        if host == "localhost":
            print(f"Local URL: http://localhost:{port}")
        else:
            print(f"Public URL after configuring TLS reverse proxy: https://{host}")
        print(f"Container status:\n{compose('ps')}")
        if bind in ("0.0.0.0", "::"):  # nosec B104 - warns about wide host binding
            print(f"Reverse-proxy upstream: http://<server-private-ip>:{port} (binds {bind})")
        else:
            print(f"Reverse-proxy upstream: http://{bind}:{port}")
        print("Commands: ./generate_config.sh --check | ./update.sh --check | ./update.sh")
        info("Backups are optional. Configure Backup Locations and Rules in the portal if required.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install or safely resume OpenSigWeave")
    parser.add_argument("--channel", choices=CHANNELS, help="Must equal the checkout branch")
    parser.add_argument("--hostname", help="Public hostname, default: detected FQDN or localhost")
    parser.add_argument("--admin-email", help="Initial administrator email")
    parser.add_argument("--allowed-hosts", help="Comma-separated portal hostnames, without scheme/port/wildcard")
    parser.add_argument("--trusted-proxy-cidrs", help="Comma-separated trusted proxy IPs/CIDRs (empty by default)")
    parser.add_argument("--generate-admin-password", action="store_true",
                        help="Save a generated first-login password in a 0600 file")
    args = parser.parse_args(argv)
    try:
        perform_install(args.channel, hostname=args.hostname,
                        admin_email=args.admin_email, allowed_hosts=args.allowed_hosts,
                        trusted_proxy_cidrs=args.trusted_proxy_cidrs,
                        generate_admin_password=args.generate_admin_password)
        return 0
    except (DeploymentError, OSError, EOFError, KeyboardInterrupt, ValueError) as exc:
        code = exc.code if isinstance(exc, DeploymentError) else 4
        print(f"[FAIL] {exc}", file=sys.stderr)
        print("Named data volumes were not removed. Resolve the issue and rerun ./install.sh.", file=sys.stderr)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
