# In-application Backup Locations, Rules, and Recovery

> **Status (dev):** Implemented as a container-native replacement for the
> rule-managed host operator. Unit tests cover orchestration and destination
> handling. A real NAS test and an isolated cross-host recovery drill are still
> **UNTESTED**. Do not call this a fully accepted disaster-recovery solution.

## Operation

The standard `compose.yaml` already mounts the named volume
`opensigweave-backups` at `/app/backups`. Backups are created by the
OpenSigWeave application scheduler (default 20-second tick). No root daemon,
systemd unit, host-mounted SMB share, privileged container, or Docker socket
is necessary. The container image installs `age` and the PostgreSQL client.

All existing Backup Locations (Local or SMB), Backup Rules (manual/scheduled),
History and retention records remain in PostgreSQL. A run starts `Pending`;
the scheduler claims it, creates a PostgreSQL custom-format dump plus the
signature and identity-provider storage snapshot, verifies its manifest,
encrypts with age, decrypts it with the configured recovery identity to verify
it, and atomically publishes ciphertext to the selected Local/SMB destination.
Only a completed, verified publication becomes `Success`. A failed or
interrupted run is marked `Failed`; no partial destination is treated as
successful. Database-wide run reservation prevents parallel backups.

### Consistency boundary

Web requests obtain a **shared** `flock` on
`/app/backups/.snapshot-gate.lock`; the snapshot process obtains an exclusive
lock while dumping PostgreSQL and copying both persistent stores. The same
file/inode must be shared by ALL application instances and Uvicorn workers;
separate Compose projects with independent backup volumes **must not** write
to the same PostgreSQL/files during backups. External processes that write
directly to the DB/volumes are not covered by the lock; stop them or establish
a shared write barrier. HTTP clients can experience a pause during snapshots.
Scheduling and mail-adapter deployment must account for that pause.

The on-container backup is an **application-profile** archive, not the older
host-created **full** archive: it contains the PostgreSQL dump and both file
stores, but deliberately **does not** include a host `.env` or Docker image
export. Keep an off-host copy of the exact deployable source/Compose files,
your protected `.env`/secrets and age private identity. Without those,
restoring a running instance cannot be guaranteed.

## Required configuration

Add to the installation's protected `.env` (mode 0600):

```dotenv
BACKUP_AGE_RECIPIENT="age1YOUR_PUBLIC_RECIPIENT"
BACKUP_AGE_IDENTITY="AGE-SECRET-KEY-YOUR_PRIVATE_RECOVERY_IDENTITY"
BACKUP_RULE_SCHEDULER_ENABLED="true"
BACKUP_LOCAL_ROOT="/app/backups"
BACKUP_WORK_ROOT="/app/backups"
```

Generate a real key pair using `age-keygen`. The strings above are **NOT**
usable keys. Store the identity OFF-HOST too, preferably encrypted or in an
offline password manager. The private identity is used only to verify/decrypt
archives, never included in the backup payload. Alternatively set
`BACKUP_AGE_IDENTITY_FILE` to a private (mode 0600) file already accessible
inside the container, and leave `BACKUP_AGE_IDENTITY` blank; exactly one
source must be present. Do not mount Docker sockets or privileged host
paths to supply it. Before starting the scheduler, ensure disk capacity for
the plaintext dump, staged files, archive and verification copy; staging files
are created with restrictive permissions inside a private directory and removed
after each run. Keep the backups volume on trustworthy storage.

SMB credentials remain encrypted at rest using `BACKUP_LOCATIONS_KEY` (a
separately generated Fernet key) and are never returned by the API. Keep a
separate offline copy of this key, since restored location records are useless
without it. SMB uses direct SMB3 encrypted and signed connections via Python
`smbclient` (TCP 445); no host mount or password-bearing command line.
Configure the share and optional subdirectory in **Backup Locations** and
test the connection before running a rule.

## Backup Rules

Each rule selects one enabled Local/SMB Location, full application content,
UTC schedule and retention (by count, age, or both). Daily, weekly, monthly
(day 1–28) and interval (15–43,200 minutes) schedules are supported.
`Run Now` is asynchronous and returns a pending run; **Backup History**
shows the result. The application worker processes pending manual jobs even when
`BACKUP_RULE_SCHEDULER_ENABLED=false`. This environment variable is an
operator-level safety pause for **automatic dispatch** (for example while
retiring a legacy host timer), not a global Backup Settings policy. Normal
automatic execution is controlled by each rule's Enabled state and schedule.
The deprecated `/api/admin/backups/settings` endpoint remains a read-only
HTTP 410 compatibility tombstone; it cannot change rule configuration.
The former global directory, automatic and retention fields are not persisted
in the application database. Migration 0017 already creates the complete
per-rule schedule, content and retention columns; no destructive schema
change or implicit import of host `backup.env` settings is safe or required.
Transfer any former host-only schedule manually into a named Backup Rule
before retiring the legacy timer.

Local files are stored at `/app/backups/<location-id>/` and published
atomically with private permissions. SMB uploads use a temporary filename,
readback hash/size verification and rename. Retention removes only
database-owned archive names, never a foreign file; the newest successful
backup remains protected. A previously verified ciphertext backup can be
downloaded from History.

## Recovery and migration

**Never restore into the production Compose project.** New application
archives support the following safe offline preparation command, run in a
trusted environment with Python dependencies and `age` installed:

```bash
python backup_cli.py restore-staging \
  /safe/offhost/opensigweave-<rule-id>-<run-id>.age \
  --identity /safe/offhost/age-identity.txt \
  --target-dir /safe/new-recovery-directory
```

The destination must not exist. The command decrypts, checks every manifest
digest and extracts regular files only. It never starts containers, rewrites
running databases or uses Docker sockets. To bring up a recovered instance,
create a **new isolated Compose project** from the matching saved source and
protected `.env`, restore `database/opensigweave.dump` with `pg_restore`
into that project's empty PostgreSQL database, and restore
`data/signatures` and `data/idp-config` into its separate persistent
volumes. Validate ownership, Alembic revision, application health, records,
images and sign-in before changing live routing. **Automated creation of a
running Compose instance from application-profile archives is UNTESTED /
NOT IMPLEMENTED.** The CLI intentionally does not claim to do that.

Older host-created **full** `.age` archives retain their legacy
`backup_cli.py restore` path for isolated fresh deployment. Do not delete
these archives, age identities, legacy tooling or host service until the
container-native workflow and a real restore have passed acceptance testing.
The optional `compose.backup-control.yaml` is **not needed** by the new
workflow. **Disable the legacy backup timer before enabling native scheduled
backups**, to prevent overlapping snapshots. Do not delete the legacy backup
service or any verified legacy archives yet. Remove the override from the
Compose invocation, recreate the app, configure age recovery settings, run a
manual test, inspect and download the ciphertext, and test a recovery on a
different isolated host. After the new recovery has been accepted, stop and
remove the obsolete host operator service and legacy systemd definitions.

## Updates

`update.sh` now creates an encrypted, independently verified
`opensigweave-before-update-*.age` inside the shared
`opensigweave-backups` named volume through `docker compose exec`, using
the same cross-process snapshot lock. No `OSW_BACKUP_CONFIG`, `findmnt`,
`age` installation on the host, or remote filesystem mount is required.
**Export this pre-update archive to off-host storage before relying on it for
disaster recovery.** A first upgrade from an older container that does not
yet implement `backup_cli.py create-application` will refuse the new update
path: take and verify a legacy full backup first, perform a controlled manual
upgrade, and only then use the container-native updater.

## Verification

- `python -m unittest tests.test_backup_engine tests.test_backup_rules tests.test_backup_locations`
- `python -m unittest tests.test_backup_encryption tests.test_backup_archive tests.test_backup_restore`
- `python -m unittest tests.test_deployment_manager`
- GitHub Actions `Tests` workflow includes old full-archive Docker integration.
  **This is not a live SMB or cross-host recovery test of the new archive profile.**

**Known limits:** Long snapshot pauses, out-of-process data writers, SMB
reconnect under real packet loss, cold recovery to fresh services, and
cross-host disk capacity are not yet covered by production integration tests.
