"""Safe channel-bound OpenSigWeave updater: preflight, verified age backup, migration."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys

from deploy_common import (
    ROOT, DeploymentError, check_migration, compose, current_channel,
    current_sha, docker, exclusive_lock, git, healthcheck, info, ok, parse_env,
    pristine, remote_sha, run, service_state, system_checks, validate_config, warn,
)
from config_manager import ENV, migrate_config

BACKUP_CONFIG = Path(os.environ.get("OSW_BACKUP_CONFIG", "/etc/opensigweave/backup.env"))
CRITICAL_PATHS = ("migrations/", "adapters/", "rspamd/", "core/engine_contract.py",
                  "compose.yaml", "Dockerfile", ".env.example", "backup_")
REMOTE_FS = {"nfs", "nfs4", "cifs", "smb3", "fuse.sshfs", "fuse.rclone"}


def preflight() -> dict[str, str]:
    system_checks()
    pristine()
    env = parse_env(ENV)
    validate_config(env)
    compose("config", "--quiet")
    for service in ("postgres", "opensigweave"):
        service_state(service)
    revision = check_migration()
    db_version = compose(
        "exec", "-T", "postgres", "sh", "-c",
        'psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SHOW server_version"',
    )
    app_id = compose("ps", "-q", "opensigweave")
    mounts = json.loads(docker("inspect", "--format", "{{json .Mounts}}", app_id))
    for destination in ("/data/signatures", "/app/.idp"):
        if not any(x.get("Destination") == destination and x.get("Type") == "volume" for x in mounts):
            raise DeploymentError(f"Persistent data volume missing: {destination}", 6)
    info(f"PostgreSQL={db_version} Alembic={revision}; persistent mounts verified")
    return env


def backup_options() -> tuple[Path, str, Path]:
    if BACKUP_CONFIG.is_symlink() or not BACKUP_CONFIG.is_file():
        raise DeploymentError(
            f"Encrypted backup config missing: {BACKUP_CONFIG}. "
            "Create protected backup.env with BACKUP_DIRECTORY, AGE_RECIPIENT and AGE_IDENTITY.", 7
        )
    mode = stat.S_IMODE(BACKUP_CONFIG.stat().st_mode)
    if mode & 0o077:
        raise DeploymentError("Backup configuration must have permissions 0600", 7)
    conf = parse_env(BACKUP_CONFIG)
    try:
        target = Path(conf["BACKUP_DIRECTORY"]).expanduser()
        recipient = conf["AGE_RECIPIENT"]
        identity = Path(conf["AGE_IDENTITY"]).expanduser()
    except KeyError as exc:
        raise DeploymentError(f"Missing backup setting: {exc}", 7) from exc
    if not target.is_absolute() or target.is_symlink() or not target.is_dir():
        raise DeploymentError("BACKUP_DIRECTORY must be an existing absolute directory (not symlink)", 7)
    if ROOT == target or ROOT in target.parents:
        raise DeploymentError("Backup directory must be outside the application checkout", 7)
    if not identity.is_absolute() or identity.is_symlink() or not identity.is_file():
        raise DeploymentError("Offline age identity file is missing or unsafe", 7)
    if stat.S_IMODE(identity.stat().st_mode) & 0o077:
        raise DeploymentError("age identity permissions must be 0600", 7)
    if not recipient.startswith("age1") or len(recipient) < 20:
        raise DeploymentError("Invalid age public recipient", 7)
    if not shutil.which("age"):
        raise DeploymentError("age is not installed", 7)
    filesystem = run("findmnt", "-n", "-o", "FSTYPE", "--target", str(target))
    if filesystem not in REMOTE_FS:
        raise DeploymentError("Backups require a dedicated mounted NFS/SMB/SSHFS/rclone target", 7)
    # A directory within a remotely mounted filesystem is allowed, but never local disk.
    if not os.access(target, os.W_OK):
        raise DeploymentError("Backup target is not writable", 7)
    return target, recipient, identity


def create_backup() -> Path:
    target, recipient, identity = backup_options()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = target / f"opensigweave-before-update-{stamp}-{current_sha()[:12]}.age"
    if backup.exists() or backup.is_symlink():
        raise DeploymentError("Backup path already exists; refusing overwrite", 7)
    info(f"Creating encrypted verified backup: {backup}")
    run(sys.executable, str(ROOT / "backup_cli.py"), "create", "--project-dir",
        str(ROOT), "--output", str(backup), "--recipient", recipient,
        "--identity", str(identity), timeout=7200)
    if not backup.is_file() or backup.stat().st_size == 0:
        raise DeploymentError("Backup output missing or empty", 7)
    run(sys.executable, str(ROOT / "backup_cli.py"), "verify", str(backup),
        "--identity", str(identity), timeout=7200)
    ok(f"Full age backup independently verified: {backup}")
    return backup


def validate_target(target: str) -> list[str]:
    if not re.fullmatch("[0-9a-f]{40,64}", target):
        raise DeploymentError("Invalid remote Git object ID", 4)
    git("cat-file", "-e", f"{target}^{{commit}}")
    ancestor = git("merge-base", "--is-ancestor", "HEAD", target) if False else None
    # merge-base --is-ancestor is a predicate, not a stdout-producing command.
    result = __import__("subprocess").run(
        ["git", "merge-base", "--is-ancestor", "HEAD", target],
        cwd=ROOT, stdout=__import__("subprocess").DEVNULL,
        stderr=__import__("subprocess").DEVNULL, check=False,
    )
    if result.returncode != 0:
        raise DeploymentError("Remote is not a fast-forward; manual review required", 4)
    files = git("diff", "--name-only", "HEAD", target).splitlines()
    return [name for name in files if name.startswith(CRITICAL_PATHS)]


def show_plan(channel: str, installed: str, remote: str, critical: list[str] | None) -> None:
    print(f"Channel: {channel}\nInstalled commit: {installed}\nRemote commit:    {remote}")
    if installed == remote:
        ok("No update available")
        return
    info("A newer or different remote commit exists")
    if critical is None:
        warn("Target not fetched locally. Breaking changes and release notes cannot yet be assessed.")
        print("Run ./update.sh --prefetch, then --dry-run to review the full diff.")
        return
    print("Changes:\n" + (git("log", "--format=%h %s", "-n", "12", f"{installed}..{remote}") or "(none)"))
    if critical:
        warn("Critical paths changed; review migration/adapters before activation:")
        for name in critical[:40]:
            print(f"  {name}")
    else:
        ok("No adapter, migration, Compose or configuration paths changed")
    print("Review docs/UPGRADE.md and the relevant release notes before applying.")


def apply_update(channel: str, remote: str, *, yes: bool, approve_breaking: bool, skip_start: bool) -> None:
    with exclusive_lock():
        env = preflight()
        installed = current_sha()
        if installed == remote:
            ok("Already up-to-date")
            return
        # Fetch only the chosen channel; never write to remote or switch local branches.
        info(f"Fetching authorized {channel} source without executing downloaded scripts")
        git("fetch", "--no-tags", "origin", f"refs/heads/{channel}:refs/remotes/origin/{channel}")
        fetched = git("rev-parse", f"refs/remotes/origin/{channel}")
        if fetched != remote:
            raise DeploymentError("Remote moved during update; rerun --check", 4)
        critical = validate_target(fetched)
        show_plan(channel, installed, fetched, critical)
        if critical and not approve_breaking:
            raise DeploymentError(
                "Critical changes need --approve-breaking after manual review", 4
            )
        if not yes:
            if input("Create mandatory backup and update this checkout? [y/N]: ").strip().lower() != "y":
                raise DeploymentError("Update cancelled", 4)
        backup_options()  # Fail before attempting code or service modification.
        backup = create_backup()
        info("Fast-forwarding only the current local branch")
        git("merge", "--ff-only", fetched)
        if current_channel() != channel:
            raise DeploymentError("Channel changed unexpectedly; stop for manual recovery", 4)
        # No service is stopped until new source/Compose are checked and built.
        env = parse_env(ENV)
        validate_config(env)
        migrate_config(yes=True)
        compose("config", "--quiet")
        info("Building the new application image locally (no dev/beta registry images)")
        compose("build", "--pull", "opensigweave", timeout=3600)
        info("Stopping application to serialize Alembic migration")
        compose("stop", "--timeout", "30", "opensigweave", timeout=120)
        try:
            compose("run", "--rm", "--no-deps", "opensigweave", "alembic", "upgrade", "head", timeout=1200)
            # One-off migration has terminated; no other web worker may migrate concurrently.
            revision = compose("run", "--rm", "--no-deps", "opensigweave", "alembic", "current", timeout=120)
            info(f"Alembic after migration: {revision}")
        except DeploymentError as exc:
            raise DeploymentError(
                f"Database migration failed. Do NOT roll back only application code. "
                f"Keep service stopped; use verified backup {backup} and the fresh-target "
                f"restore procedure. Reason: {exc}", 8
            ) from exc
        if skip_start:
            warn("Update prepared and migrated, but application intentionally remains stopped (--skip-start).")
            print(f"Commit: {current_sha()}\nVerified recovery backup: {backup}")
            return
        try:
            info("Starting updated OpenSigWeave")
            compose("up", "-d", "--no-deps", "--force-recreate", "--wait",
                    "--wait-timeout", "240", "opensigweave", timeout=600)
            healthcheck(parse_env(ENV))
        except DeploymentError as exc:
            raise DeploymentError(
                f"Post-migration startup/healthcheck failed. No automatic rollback is safe. "
                f"Restore to a fresh target using {backup}, then review routing. Reason: {exc}", 8
            ) from exc
        ok(f"Update successful: channel={channel} commit={current_sha()}")
        print(f"Verified recovery backup: {backup}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check, prefetch and safely update OpenSigWeave")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Read-only remote version check")
    mode.add_argument("--dry-run", action="store_true", help="Read-only preflight and local diff preview")
    mode.add_argument("--prefetch", action="store_true", help="Fetch Git commits without switching code or services")
    parser.add_argument("--skip-start", action="store_true", help="Apply update/migrations but leave app stopped")
    parser.add_argument("--version", action="store_true", help="Print checkout version, SHA and channel")
    parser.add_argument("--yes", action="store_true", help="Confirm ordinary update without prompting")
    parser.add_argument("--approve-breaking", action="store_true", help="Explicitly approve reviewed critical paths")
    args = parser.parse_args(argv)
    try:
        channel = current_channel()
        installed = current_sha()
        if args.version:
            print(f"OpenSigWeave checkout {channel}: {installed}")
            return 0
        pristine()
        remote = remote_sha(channel)
        if args.check:
            critical = [] if remote == installed else None
            try:
                git("cat-file", "-e", f"{remote}^{{commit}}")
                critical = validate_target(remote)
            except DeploymentError:
                pass
            show_plan(channel, installed, remote, critical)
            return 0
        if args.dry_run:
            preflight()
            critical = [] if remote == installed else None
            try:
                critical = validate_target(remote)
            except DeploymentError:
                pass
            show_plan(channel, installed, remote, critical)
            backup_options()
            ok("Read-only preflight completed; no containers, code or data changed")
            return 0
        if args.prefetch:
            with exclusive_lock():
                preflight()
                git("fetch", "--no-tags", "origin", f"refs/heads/{channel}:refs/remotes/origin/{channel}")
                fetched = git("rev-parse", f"refs/remotes/origin/{channel}")
                if fetched != remote:
                    raise DeploymentError("Remote moved during prefetch; retry", 4)
                critical = validate_target(fetched)
                show_plan(channel, installed, fetched, critical)
                ok("Source fetched only; no checkout, containers or volumes changed")
            return 0
        apply_update(channel, remote, yes=args.yes,
                     approve_breaking=args.approve_breaking, skip_start=args.skip_start)
        return 0
    except (DeploymentError, OSError, ValueError, KeyboardInterrupt, EOFError) as exc:
        code = exc.code if isinstance(exc, DeploymentError) else 4
        print(f"[FAIL] {exc}", file=sys.stderr)
        print("No volumes were removed. Review status and verified backup before manual recovery.", file=sys.stderr)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
