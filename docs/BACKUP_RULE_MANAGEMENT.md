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

## Optional encryption configuration and private-key export

No Backup Location, Rule, age identity, or backup storage is required for
installation or for an update when **no rule is enabled**. The first actual
backup automatically generates an age key pair inside the container using
`age-keygen`, with a **0600** private identity under
`/app/backups/.recovery/age-identity.txt` on the persistent backups volume.
The directory is private (0700), and the identity is never included in the
application-profile backup payload or printed to the console.

**Export the private identity off-host immediately after your first backup:**

```bash
mkdir -m 700 -p /media/offline/opensigweave-recovery
./export_backup_key.sh /media/offline/opensigweave-recovery/age-identity.txt
```

This securely copies the file without displaying the private key, refuses
to overwrite an existing destination and requires a private target directory.
For full disaster recovery also separately escrow the protected `.env`,
matching source/Compose configuration and encrypted archives. A key saved
only on the backup volume does **not** protect against losing that volume.

Existing age installations remain compatible: a configured
`BACKUP_AGE_RECIPIENT` plus exactly one of
`BACKUP_AGE_IDENTITY` or `BACKUP_AGE_IDENTITY_FILE` (0600, accessible inside
the container) is honored unchanged. An incomplete legacy key configuration
is a real backup failure; no replacement key is generated.

`BACKUP_LOCATIONS_KEY` is generated automatically as an independent Fernet
key during first installation or safe `generate_config.sh --migrate`. It
protects SMB credentials stored in PostgreSQL. Never rotate it without
re-encrypting those stored credentials, and escrow it with the protected
`.env`. SMB requires encrypted and signed SMB3 and uses no host mount.

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

## Updates with optional pre-update backup

`update.sh` queries enabled Backup Rules/Locations in the **running**
OpenSigWeave container. If no active rule is configured, it continues normally
without running a backup, requiring age configuration, or emitting warnings.

If an enabled rule is present, the updater selects it by
`PREUPDATE_BACKUP_RULE_ID` (when supplied in the protected `.env`) or,
by default, case-insensitive rule name followed by ID. Invalid preferred
rules or disabled/broken destinations fail closed. The existing backup engine
creates a full encrypted application archive and publishes it to the rule's
Local or SMB destination. The updater re-reads the **retained archive**,
checks ciphertext size and SHA256, extracts it into isolated temporary
storage, verifies the manifest, and validates the dump with `pg_restore --list`.
The exact rule, destination, archive identifier and result are journaled.

If this configured backup fails, no update begins until an interactive
administrator explicitly answers `y` to
`Backup failed. Continue updating without a backup? [y/N]`.
`--yes` alone does not override a backup failure; in a noninteractive
process only `--yes --continue-without-backup` authorizes an override.
An abort preserves source and volumes. Successful and intentionally skipped
backups are represented by different journal statuses.

The archive profile contains PostgreSQL, signatures, and identity-provider
files. It **does not** include the protected `.env` or Docker image. A full
cross-host recovery procedure is not yet automated or acceptance-tested.

## Verification

- `python -m unittest tests.test_backup_engine tests.test_backup_rules tests.test_backup_locations`
- `python -m unittest tests.test_backup_encryption tests.test_backup_archive tests.test_backup_restore`
- `python -m unittest tests.test_deployment_manager`
- GitHub Actions `Tests` workflow includes old full-archive Docker integration.
  **This is not a live SMB or cross-host recovery test of the new archive profile.**

**Known limits:** Long snapshot pauses, out-of-process data writers, SMB
reconnect under real packet loss, cold recovery to fresh services, and
cross-host disk capacity are not yet covered by production integration tests.
