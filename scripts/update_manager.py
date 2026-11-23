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

CRITICAL_PATHS = (
    "migrations/", "adapters/", "rspamd/", "core/engine_contract.py",
    "core/engine_service.py", "main.py", "models.py", "database.py", "alembic.ini",
    "compose.yaml", "Dockerfile", ".env.example", "backup_",
)
UPDATE_JOURNAL = ROOT / ".git" / "opensigweave-update-state.json"


def ensure_no_incomplete_update() -> None:
    """Never report 'already updated' after an interrupted source switch."""
    if UPDATE_JOURNAL.is_symlink():
        raise DeploymentError("Unsafe update journal symlink; review it manually", 8)
    if UPDATE_JOURNAL.exists():
        if not UPDATE_JOURNAL.is_file():
            raise DeploymentError("Unexpected update journal file type", 8)
        try:
            record = json.loads(UPDATE_JOURNAL.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DeploymentError("Unreadable update journal; manual inspection required", 8) from exc
        raise DeploymentError(
            "An earlier update did not finish: "
            f"stage={record.get('stage', 'unknown')}, "
            f"target={record.get('target', 'unknown')}, "
            f"verified backup={record.get('backup', 'unknown')}. "
            "Do not run another update or assume the database schema is compatible. "
            "Review source, container health and the backup; follow the documented "
            "fresh-target restore procedure when migration might have started.", 8
        )


def write_update_journal(*, channel: str, installed: str, target: str,
                         backup: Path, stage: str = "source-switch") -> None:
    """Atomic 0600 state file outside the tracked tree; never store credentials."""
    record = {
        "channel": channel, "installed": installed, "target": target,
        "backup": str(backup), "stage": stage,
    }
    if UPDATE_JOURNAL.is_symlink():
        raise DeploymentError("Unsafe update journal path", 8)
    if stage == "source-switch":
        descriptor = os.open(
            UPDATE_JOURNAL, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        destination = UPDATE_JOURNAL
    else:
        destination = UPDATE_JOURNAL.with_name(UPDATE_JOURNAL.name + ".pending")
        descriptor = os.open(
            destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if stage != "source-switch":
            os.replace(destination, UPDATE_JOURNAL)
    finally:
        if stage != "source-switch":
            destination.unlink(missing_ok=True)


def clear_update_journal() -> None:
    if UPDATE_JOURNAL.is_symlink():
        raise DeploymentError("Unsafe update journal path", 8)
    UPDATE_JOURNAL.unlink(missing_ok=True)


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


def backup_options() -> None:
    """Require in-container age recovery configuration before updates."""
    env = parse_env(ENV)
    recipient = env.get("BACKUP_AGE_RECIPIENT", "")
    inline_identity = env.get("BACKUP_AGE_IDENTITY", "")
    mounted_identity = env.get("BACKUP_AGE_IDENTITY_FILE", "")
    if not recipient.startswith("age1") or len(recipient) > 200:
        raise DeploymentError("Configure BACKUP_AGE_RECIPIENT in the protected application .env", 7)
    if bool(inline_identity) == bool(mounted_identity):
        raise DeploymentError(
            "Configure exactly one BACKUP_AGE_IDENTITY or BACKUP_AGE_IDENTITY_FILE for verification", 7
        )
    if inline_identity and not inline_identity.startswith("AGE-SECRET-KEY-"):
        raise DeploymentError("Invalid age identity configuration", 7)
    # Permissions and mounted identity availability are rechecked inside the
    # application by backup_engine before encryption or deployment changes.


def create_backup() -> Path:
    backup_options()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"opensigweave-before-update-{stamp}-{current_sha()[:12]}.age"
    backup = Path("/app/backups") / filename
    info(f"Creating age-encrypted pre-update snapshot in persistent volume: {backup}")
    try:
        compose("exec", "-T", "opensigweave", "python", "backup_cli.py",
                "create-application", "--filename", filename, timeout=7200)
        compose("exec", "-T", "opensigweave", "python", "backup_cli.py",
                "verify-application", "--filename", filename, timeout=7200)
    except DeploymentError as exc:
        raise DeploymentError(
            "Mandatory in-container age backup or verification failed. Source was not changed. "
            "Check age key configuration, PostgreSQL, persistent volumes and available disk space. "
            "Do not bypass backup verification.", 7
        ) from exc
    ok(f"Encrypted pre-update recovery backup verified in app volume: {backup}")
    return backup


def validate_target(target: str) -> list[str]:
    if not re.fullmatch("[0-9a-f]{40,64}", target):
        raise DeploymentError("Invalid remote Git object ID", 4)
    git("cat-file", "-e", f"{target}^{{commit}}")
    if git("merge-base", "HEAD", target) != current_sha():
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
        ensure_no_incomplete_update()
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
        write_update_journal(channel=channel, installed=installed, target=fetched,
                             backup=backup, stage="source-switch")
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
        write_update_journal(channel=channel, installed=installed, target=fetched,
                             backup=backup, stage="migration")
        info("Stopping application to serialize Alembic migration")
        compose("stop", "--timeout", "30", "opensigweave", timeout=120)
        try:
            compose("run", "--rm", "--no-deps", "opensigweave", "alembic", "upgrade", "head", timeout=1200)
            # One-off migration has terminated; no other web worker may migrate concurrently.
            revision = compose("run", "--rm", "--no-deps", "opensigweave", "alembic", "current", timeout=120)
            heads = compose("run", "--rm", "--no-deps", "opensigweave", "alembic", "heads", timeout=120)
            if not revision or revision.split()[0] != heads.split()[0]:
                raise DeploymentError("Post-migration Alembic revision does not match head", 8)
            info(f"Alembic after migration: {revision}")
        except DeploymentError as exc:
            raise DeploymentError(
                f"Database migration failed. Do NOT roll back only application code. "
                f"Keep service stopped; use verified backup {backup} and the fresh-target "
                f"restore procedure. Reason: {exc}", 8
            ) from exc
        write_update_journal(channel=channel, installed=installed, target=fetched,
                             backup=backup, stage="migration-complete")
        if skip_start:
            clear_update_journal()
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
        clear_update_journal()
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
            print(f"OpenSigWeave version: {git('describe', '--tags', '--always', '--dirty')} | channel: {channel} | commit: {installed}")
            return 0
        ensure_no_incomplete_update()
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
