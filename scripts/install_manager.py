"""Conservative interactive first-install and partial-install resume."""
from __future__ import annotations

import argparse
import os
import socket
import sys

from deploy_common import (
    ROOT, DeploymentError, CHANNELS, check_migration, compose, current_channel,
    current_sha, exclusive_lock, healthcheck, info, ok, parse_env, pristine,
    remote_sha, service_state, system_checks, validate_config, warn,
)
from config_manager import ENV, create_config


def port_available(bind: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in bind else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.bind((bind, port))
            return True
    except OSError:
        return False


def perform_install(channel: str | None) -> None:
    with exclusive_lock():
        pristine()
        selected = current_channel()
        if channel is None:
            print(f"Git channel: {selected}. The local checkout will NOT be switched automatically.")
            chosen = input(f"Install from [{selected}] (dev/beta/main): ").strip() or selected
        else:
            chosen = channel
        if chosen not in CHANNELS or chosen != selected:
            raise DeploymentError(
                "Requested channel does not match this checkout. Clone with "
                f"'git clone --branch {chosen} https://github.com/VeiglbergTech/OpenSigWeave.git' "
                "and run its installer; no branch was changed.", 4
            )
        if selected != "main":
            warn(f"{selected} is not a production-stable release channel")
        remote_sha(selected)  # Test Git network and authorized origin without modifying refs.
        system_checks()
        if ENV.is_symlink():
            raise DeploymentError("Refusing symlinked .env", 4)
        if ENV.exists():
            env = parse_env(ENV)
            validate_config(env)
            try:
                service_state("postgres")
                service_state("opensigweave")
            except DeploymentError:
                warn("Existing configuration or partial deployment found; resuming without replacing it")
            else:
                raise DeploymentError("Installation already healthy. Use ./update.sh instead.", 4)
        else:
            create_config()
            env = parse_env(ENV)
        validate_config(env)
        os.chmod(ENV, 0o600)
        bind = env.get("APP_BIND_ADDRESS", "127.0.0.1")
        port = int(env.get("APP_PORT", "8085"))
        if not port_available(bind, port):
            existing = compose("ps", "-q", "opensigweave")
            if not existing:
                raise DeploymentError(f"Port {bind}:{port} is occupied; choose another port", 4)
            warn("Port occupied by a partial OpenSigWeave deployment; inspecting Compose state")
        info("Validating Docker Compose")
        compose("config", "--quiet")
        info("Starting PostgreSQL (volumes will be preserved)")
        compose("up", "-d", "--wait", "--wait-timeout", "180", "postgres", timeout=360)
        service_state("postgres")
        info("Building and starting OpenSigWeave; embedded Alembic migration is serialized")
        compose("up", "-d", "--build", "--wait", "--wait-timeout", "240", "opensigweave", timeout=1800)
        healthcheck(env)
        ok("Installation successful")
        print(f"Channel: {selected} | Commit: {current_sha()}")
        print(f"Directory: {ROOT} | URL: https://{env['APP_ALLOWED_HOSTS'].split(',')[0]}")
        print(f"Container status:\n{compose('ps')}")
        print(f"Proxy upstream: http://{bind}:{port}; terminate TLS and configure trusted proxy access.")
        print("Commands: ./generate_config.sh --check | ./update.sh --check | ./update.sh")
        print("Before updates, configure an offline age identity and a remotely mounted backup destination.")
        print("See the OpenSigWeave-Docs installation, backup and restore guides.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install OpenSigWeave from the current Git channel")
    parser.add_argument("--channel", choices=CHANNELS, help="Must equal checkout branch")
    args = parser.parse_args(argv)
    try:
        perform_install(args.channel)
        return 0
    except (DeploymentError, OSError, EOFError, KeyboardInterrupt, ValueError) as exc:
        code = exc.code if isinstance(exc, DeploymentError) else 4
        print(f"[FAIL] {exc}", file=sys.stderr)
        print("No data volumes were deleted. Correct the problem and rerun ./install.sh.", file=sys.stderr)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
