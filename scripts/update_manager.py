"""Channel-bound, optional-backup updater with fail-closed migration handling."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
import re
import sys

from deploy_common import (
    ROOT, DeploymentError, check_migration, compose, current_channel,
    current_sha, docker, exclusive_lock, git, healthcheck, info, ok, parse_env,
    pristine, remote_sha, run, service_state, system_checks, validate_config, warn,
    verify_release_image,
)
from config_manager import ENV, migrate_config
from release_target import latest_release, release_tag_sha

CRITICAL_PATHS = (
    "migrations/", "adapters/", "rspamd/", "core/engine_contract.py",
    "core/engine_service.py", "main.py", "models.py", "database.py", "alembic.ini",
    "compose.yaml", "Dockerfile", ".env.example", "backup_",
)
UPDATE_JOURNAL = ROOT / ".git" / "opensigweave-update-state.json"


def ensure_no_incomplete_update() -> None:
    """Never assume a partially migrated schema can be automatically rolled back."""
    if UPDATE_JOURNAL.is_symlink():
        raise DeploymentError("Unsafe update journal symlink; review it manually", 8)
    if not UPDATE_JOURNAL.exists():
        return
    if not UPDATE_JOURNAL.is_file():
        raise DeploymentError("Unexpected update journal type", 8)
    try:
        record = json.loads(UPDATE_JOURNAL.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DeploymentError("Unreadable update journal; manual inspection required", 8) from exc
    backup = record.get("backup", {})
    if isinstance(backup, str):
        backup = {"status": "verified", "file_key": backup} if backup else {"status": "unknown"}
    raise DeploymentError(
        "An earlier update did not finish: "
        f"stage={record.get('stage', 'unknown')}, "
        f"target={record.get('target', 'unknown')}, "
        f"backup_status={backup.get('status', 'unknown')}. "
        "Do not start another update or revert application code on a potentially "
        "migrated database. Inspect the journal, database schema and containers; "
        "use a fresh-target restore only if a verified archive actually exists.", 8
    )


def write_update_journal(*, channel: str, installed: str, target: str,
                         backup: dict, stage: str = "source-switch") -> None:
    """Atomically persist backup provenance and the current update stage (0600)."""
    record = {
        "channel": channel, "installed": installed, "target": target,
        "backup": backup, "stage": stage,
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


def _backup_command(*args: str) -> dict:
    """Parse structured responses from the existing in-container Backup Rules CLI."""
    try:
        raw = compose("exec", "-T", "opensigweave", "python", "backup_cli.py",
                      "preupdate", *args, timeout=7200)
        result = json.loads(raw)
    except (DeploymentError, ValueError, TypeError) as exc:
        raise DeploymentError(
            "Cannot query or execute the in-container backup service. "
            "Check PostgreSQL, running containers and application logs.", 7
        ) from exc
    if not isinstance(result, dict) or result.get("status") not in {
        "not_configured", "configured", "verified", "failed"
    }:
        raise DeploymentError("Invalid response from backup service", 7)
    return result


def detect_backup() -> dict:
    """Read-only check, including upgrades from older app images lacking this CLI.

    A legacy container may not contain the new preupdate command yet. Only
    PostgreSQL can then establish that zero enabled Backup Rules exist;
    otherwise fail closed and ask for an explicit backup override.
    """
    try:
        return _backup_command("--check")
    except DeploymentError as previous:
        sql_table = "SELECT to_regclass('public.backup_rules')"
        try:
            present = compose(
                "exec", "-T", "postgres", "sh", "-c",
                'psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "' + sql_table + '"',
                timeout=120,
            ).strip()
            if not present:
                return {"status": "not_configured"}
            count = compose(
                "exec", "-T", "postgres", "sh", "-c",
                'psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c '
                '"SELECT count(*) FROM public.backup_rules WHERE is_enabled IS TRUE"',
                timeout=120,
            ).strip()
            if count == "0":
                return {"status": "not_configured"}
        except DeploymentError:
            pass
        raise DeploymentError(
            "Cannot verify Backup Rules using this application image; "
            "a configured rule or an unavailable backup service must be "
            "treated as a backup failure.", 7
        ) from previous


def create_backup() -> dict:
    """Execute selected active rule and verify the retained encrypted artifact."""
    result = _backup_command()
    if result["status"] != "verified":
        raise DeploymentError(
            result.get("reason", "Configured backup did not produce a verified archive"), 7
        )
    ok("Encrypted pre-update backup verified: "
       f"rule={result['rule_name']} location={result['location_name']} "
       f"archive={result['file_key']}")
    return result


def select_backup_for_update(*, yes: bool, continue_without_backup: bool) -> dict:
    """No rule is normal; a failed rule requires an explicit separate decision."""
    try:
        detected = detect_backup()
        if detected["status"] == "not_configured":
            info("No backup configured. Continuing normally.")
            return {"status": "not_configured"}
        if detected["status"] != "configured":
            raise DeploymentError(detected.get("reason", "Backup configuration is unusable"), 7)
        info("Pre-update backup selected: "
             f"rule={detected['rule_name']} location={detected['location_name']}")
        return create_backup()
    except DeploymentError as exc:
        warn(f"Pre-update backup failed: {exc}")
        if continue_without_backup:
            info("Explicit override accepted: continuing without a verified recovery point")
            return {"status": "failed_and_overridden"}
        if sys.stdin.isatty():
            try:
                answer = input("Backup failed. Continue updating without a backup? [y/N] ")
            except (EOFError, KeyboardInterrupt):
                answer = ""
            if answer.strip().lower() == "y":
                info("Administrator explicitly accepted an update without a recovery point")
                return {"status": "failed_and_overridden"}
        raise DeploymentError(
            "Backup failed and continuing without a backup was not authorized. "
            "Source, containers and data are unchanged.", 7
        ) from exc


def recovery_guidance(backup: dict) -> str:
    if backup.get("status") == "verified":
        return ("Verified archive " + backup.get("file_key", "(unknown)") +
                " at Backup Location " + backup.get("location_name", "(unknown)") +
                "; follow the documented fresh-target restore procedure.")
    return ("No verified recovery point exists. Do not automatically roll back code "
            "after a schema migration; investigate and recover manually.")


def pin_release_image_tag(tag: str) -> None:
    """Atomically persist the exact published image tag, never a moving alias."""
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag):
        raise DeploymentError("Invalid image tag", 4)
    if ENV.is_symlink() or not ENV.is_file():
        raise DeploymentError("Missing or symlinked .env", 4)
    original = ENV.read_text(encoding="utf-8")
    lines = original.splitlines()
    result = []
    replaced = 0
    for line in lines:
        if re.match(r"^\s*OSW_IMAGE_TAG\s*=", line):
            result.append(f'OSW_IMAGE_TAG="{tag}"')
            replaced += 1
        else:
            result.append(line)
    if replaced > 1:
        raise DeploymentError("Duplicate OSW_IMAGE_TAG entries", 4)
    if not replaced:
        result.append(f'OSW_IMAGE_TAG="{tag}"')
    new_text = "\n".join(result) + "\n"
    if new_text == original:
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".env.release-", dir=ENV.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(new_text)
            file.flush()
            os.fsync(file.fileno())
        validate_config(parse_env(Path(temporary)))
        if ENV.is_symlink():
            raise DeploymentError("Refusing to overwrite symlinked configuration", 4)
        os.replace(temporary, ENV)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def checkout_target_channel(channel: str, target: str) -> None:
    """Switch only when the target is reachable without rewriting branch history."""
    if current_channel() == channel:
        return
    if channel != "beta":
        raise DeploymentError("Cross-channel main updates require a separate release checkout", 4)
    branches = git("branch", "--list", "beta").strip()
    if branches:
        local_beta = git("rev-parse", "refs/heads/beta")
        if git("merge-base", "beta", target) != local_beta:
            raise DeploymentError("Existing beta branch cannot fast-forward; use an isolated test checkout", 4)
        git("switch", "beta")
    else:
        git("switch", "--create", "beta", "--track", "origin/beta")


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


def apply_update(channel: str, remote: str, *, yes: bool,
                 approve_breaking: bool, skip_start: bool,
                 continue_without_backup: bool = False,
                 release_tag: str | None = None) -> None:
    with exclusive_lock():
        ensure_no_incomplete_update()
        env = preflight()
        installed = current_sha()
        if installed == remote and current_channel() == channel and channel == "main":
            ok("Already at the published main release")
            return
        if installed == remote and channel == "beta":
            # A matching checkout may still run the old main/alpha GHCR image.
            # Beta always rebuilds and checks the local Docker image.
            info("Beta Git commit matches; rebuilding and validating the local Docker image")
        info(f"Fetching authorized {channel} source without executing downloaded scripts")
        if release_tag:
            if channel != "main":
                raise DeploymentError("A published release tag can only be used on main", 4)
            git("fetch", "--no-tags", "origin",
                f"refs/tags/{release_tag}:refs/tags/{release_tag}")
            fetched = git("rev-parse", f"refs/tags/{release_tag}^{{commit}}")
            # Verify that a release tag is an ancestor of the authorized main.
            git("fetch", "--no-tags", "origin", "refs/heads/main:refs/remotes/origin/main")
            main_head = git("rev-parse", "refs/remotes/origin/main")
            if git("merge-base", fetched, main_head) != fetched:
                raise DeploymentError("Published tag is not on the authorized main history", 4)
        else:
            git("fetch", "--no-tags", "origin", f"refs/heads/{channel}:refs/remotes/origin/{channel}")
            fetched = git("rev-parse", f"refs/remotes/origin/{channel}")
        if fetched != remote:
            raise DeploymentError("Remote moved or release tag changed; recheck before applying", 4)
        critical = validate_target(fetched)
        show_plan(channel, installed, fetched, critical)
        if critical:
            warn("Critical changes detected; migration and backup safeguards remain active")
        if not yes and input("Apply this update? [y/N]: ").strip().lower() != "y":
            raise DeploymentError("Update cancelled", 4)
        if channel == "main":
            # Pin the exact published version BEFORE changing source or schema.
            if not release_tag:
                raise DeploymentError("Main updates require an approved published release tag", 4)
            info("Pre-pulling and verifying the exact published release image")
            os.environ["OSW_IMAGE_TAG"] = release_tag
            compose("pull", "opensigweave", timeout=1800)
            verify_release_image({**env, "OSW_IMAGE_TAG": release_tag}, fetched)
        backup = select_backup_for_update(
            yes=yes, continue_without_backup=continue_without_backup,
        )
        if backup["status"] != "verified":
            info("No verified pre-update recovery point exists for this update.")
        write_update_journal(channel=channel, installed=installed, target=fetched,
                             backup=backup, stage="source-switch")
        info("Checking out the authorized channel and fast-forwarding its source")
        checkout_target_channel(channel, fetched)
        git("merge", "--ff-only", fetched)
        if current_channel() != channel:
            raise DeploymentError("Checkout channel changed unexpectedly; inspect deployment", 8)
        env = parse_env(ENV)
        validate_config(env)
        migrate_config(yes=True)
        if release_tag:
            pin_release_image_tag(release_tag)
        compose("config", "--quiet")
        if channel == "main":
            info("Using pre-pulled, SHA-verified GHCR image (no build)")
        else:
            info("Building unreleased development image")
            compose("build", "--pull", "opensigweave", timeout=3600)
        write_update_journal(channel=channel, installed=installed, target=fetched,
                             backup=backup, stage="migration")
        info("Stopping application to serialize Alembic migration")
        compose("stop", "--timeout", "30", "opensigweave", timeout=120)
        try:
            compose("run", "--rm", "--no-deps", "opensigweave", "alembic", "upgrade", "head", timeout=1200)
            revision = compose("run", "--rm", "--no-deps", "opensigweave", "alembic", "current", timeout=120)
            heads = compose("run", "--rm", "--no-deps", "opensigweave", "alembic", "heads", timeout=120)
            if not revision or revision.split()[0] != heads.split()[0]:
                raise DeploymentError("Post-migration Alembic revision does not match head", 8)
            info(f"Alembic after migration: {revision}")
        except DeploymentError as exc:
            raise DeploymentError(
                "Database migration failed. Do NOT roll back application code on a "
                "potentially migrated database. " + recovery_guidance(backup) +
                f" Reason: {exc}", 8
            ) from exc
        write_update_journal(channel=channel, installed=installed, target=fetched,
                             backup=backup, stage="migration-complete")
        if skip_start:
            clear_update_journal()
            warn("Update migrated; application intentionally remains stopped (--skip-start).")
            print(f"Commit: {current_sha()}\n{recovery_guidance(backup)}")
            return
        try:
            info("Starting updated OpenSigWeave")
            compose("up", "-d", "--no-deps", "--force-recreate", "--wait",
                    "--wait-timeout", "240", "opensigweave", timeout=600)
            healthcheck(parse_env(ENV))
        except DeploymentError as exc:
            raise DeploymentError(
                "Post-migration startup or healthcheck failed. Automatic rollback is unsafe. "
                + recovery_guidance(backup) + f" Reason: {exc}", 8
            ) from exc
        clear_update_journal()
        ok(f"Update successful: channel={channel} commit={current_sha()}")
        print(recovery_guidance(backup))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check, prefetch and safely update OpenSigWeave")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Read-only remote version check")
    mode.add_argument("--dry-run", action="store_true", help="Read-only preflight and local diff preview")
    mode.add_argument("--prefetch", action="store_true", help="Fetch Git commits without switching code or services")
    parser.add_argument("--beta", action="store_true",
                        help="Update to latest beta branch commit and build the image locally")
    parser.add_argument("--skip-start", action="store_true", help="Apply update/migrations but leave app stopped")
    parser.add_argument("--version", action="store_true", help="Print checkout version, SHA and channel")
    parser.add_argument("--yes", action="store_true", help="Confirm ordinary update without prompting")
    parser.add_argument("--approve-breaking", action="store_true", help="Explicitly approve reviewed critical paths")
    parser.add_argument("--continue-without-backup", action="store_true",
                        help="Explicitly override a failed configured backup; requires --yes")
    args = parser.parse_args(argv)
    try:
        if args.continue_without_backup and not args.yes:
            raise DeploymentError("--continue-without-backup requires --yes", 4)
        installed_channel = current_channel()
        channel = "beta" if args.beta else ("main" if not (args.check or args.dry_run or args.prefetch or args.version) else installed_channel)
        installed = current_sha()
        if args.version:
            print(f"OpenSigWeave version: {git('describe', '--tags', '--always', '--dirty')} | channel: {installed_channel} | commit: {installed}")
            return 0
        ensure_no_incomplete_update()
        pristine()
        release_tag = latest_release() if channel == "main" else None
        remote = release_tag_sha(release_tag) if release_tag else remote_sha(channel)
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
                     approve_breaking=args.approve_breaking, skip_start=args.skip_start,
                     continue_without_backup=args.continue_without_backup,
                     release_tag=release_tag)
        return 0
    except (DeploymentError, OSError, ValueError, KeyboardInterrupt, EOFError) as exc:
        code = exc.code if isinstance(exc, DeploymentError) else 4
        print(f"[FAIL] {exc}", file=sys.stderr)
        print("No named volumes were removed. Inspect the update journal before recovery; "
              "a verified backup may not exist.", file=sys.stderr)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
