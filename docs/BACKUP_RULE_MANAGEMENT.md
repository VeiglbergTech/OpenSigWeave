# Backup Rules, Scheduler, and History

## Status and boundaries

The administration portal manages **Backup Locations**, **Backup Rules**, and
**Backup History**. The legacy host-mounted backup management and fresh-target
restore remain available separately, unchanged.

A rule creates a **complete** encrypted disaster-recovery archive. Partial
component backups are deliberately unavailable: the existing verified restore
contract requires PostgreSQL, the signature volume, the identity-provider volume,
deployment configuration, and saved Docker images together. Branding, rules,
schedules and uploaded logo data are included in the PostgreSQL snapshot.

The application neither runs Docker nor receives the \`age\` private key.
The root-side host backup operator performs the consistent snapshot and age
encrypt/decrypt verification. It publishes ciphertext into a private host spool.
After the application container restarts, a coordinator streams that ciphertext
through the existing peer-credential-checked Unix socket to the selected
destination. Each destination copy is size/SHA-256 verified before the run becomes
**Success**. A failed or interrupted run is never shown as successful.

## Setup and upgrade

1. Upgrade PostgreSQL with \`alembic upgrade head\`. Migration
   \`0017_backup_rule_management\` adds \`backup_rules\`, \`backup_runs\` and
   \`backup_runtime\`, preserving existing Backup Locations and backup archives.
   Its \`backup_runtime\` singleton serializes all rule-managed dispatches.
2. Keep a protected host \`/etc/opensigweave/backup.env\` (root, mode \`0600\`)
   containing \`PROJECT_DIR\`, \`AGE_RECIPIENT\`, \`AGE_IDENTITY\`,
   \`DOCKER_BINARY\` and \`PYTHON_BINARY\`, as described in
   [BACKUP_RESTORE_DESIGN.md](BACKUP_RESTORE_DESIGN.md). Retain an OFF-HOST copy
   of the private identity for disaster recovery. **Without this file, no
   automated full backup can execute.** The previous legacy remote-mount
   directory is not required for rule-managed Local or SMB destinations.
3. Deploy the new \`scripts/backup_control_agent.py\` to the **Docker host** and
   restart \`opensigweave-backup-operator.service\`. The restricted socket
   remains accessible only by the configured non-root app UID. A matching
   application image and operator version are required.
4. Apply \`compose.backup-control.yaml\` alongside \`compose.yaml\` and recreate
   the application container. The existing \`opensigweave-backups\` volume must
   remain mounted at \`/app/backups\`. \`BACKUP_LOCAL_ROOT\` defaults to that
   path. If SMB destinations are needed, configure and escrow a separately
   generated \`BACKUP_LOCATIONS_KEY\` Fernet key in the protected app \`.env\`.
5. The host-managed encrypted spool defaults to
   \`/var/lib/opensigweave-backup-spool\` (root-only, mode \`0700\`). It is
   configurable with host \`BACKUP_MANAGED_SPOOL\`; provision sufficient
   disk space for at least one complete encrypted backup, including staging.
6. The web scheduler runs by default when \`BACKUP_CONTROL_SOCKET\` is set;
   set \`BACKUP_RULE_SCHEDULER_ENABLED=false\` to disable only rule-based
   automatic execution. Disable the legacy \`opensigweave-backup.timer\`
   independently if it is no longer needed. Legacy backup settings do not
   direct new Backup Rules to a storage location.

## Backup Rule fields

A rule has a name, optional description, enabled flag, selected Location, full
backup content, schedule and optional count/age retention limits. Both limits
can be combined. At least one is required.

Scheduling is **UTC only** to avoid ambiguous daylight-saving transitions.
Supported modes:

| Mode | Inputs | Behavior |
| --- | --- | --- |
| Daily | Hour and minute (UTC) | Next calendar day if today's time passed |
| Weekly | ISO weekday (Mon=0), hour/minute | Next matching weekday |
| Monthly | Day 1–28, hour/minute | No skipped short months |
| Interval | 15–43,200 minutes | Fixed delay from next computed execution |

The next due time is stored in PostgreSQL and survives restarts. The dispatcher
is idempotent for an occupied slot; at most one rule-managed run can be pending
or running across application workers. The host operator additionally uses the
existing \`flock\` shared with the legacy systemd backup timer.

Manual **Run Now** uses the same durable history and execution path.
The scheduler polls the host job after the web container restarts; no plaintext
archive or SMB credentials are written into run records. If the socket becomes
unavailable while a host job is running, the app waits for a definitive status
rather than incorrectly marking it successful.

## Storage and retention

- **Local:** \`/app/backups/<location-uuid>/\` inside the persistent named
  volume. The app checks ownership and 0700 permissions and uses directory
  descriptors with \`O_NOFOLLOW\`; ciphertext is published with a private
  temporary file and atomic rename.
- **SMB:** Access through \`smbclient\` with mandatory SMB 3 encryption and
  signing. Credentials are decrypted only during connection establishment.
  Ciphertext is uploaded to a temporary path and read back for verification
  before renaming. Exceptions are converted to non-secret status messages.
- Only archives matching their stored rule/run-generated filename and
  recorded location are eligible for per-rule deletion. Foreign files and
  failed backups are excluded. The newest successful backup is always kept,
  even if an age limit has expired. History is retained after pruning.
- Changing a destination with retained archives or while a backup is active
  is blocked; disabling/renaming an existing destination remains possible.
  Deletion of a referenced Location is blocked.

If transfer fails, the root-only host spool keeps verified ciphertext for
operator inspection; the run is marked **Failed** and is not made available as
a successful backup. Manually review stale spool artifacts and interrupted
temporary uploads after failures or host restarts. Do not treat the host spool
as a permanent backup destination.

## History and restore

History records include the rule and location name at execution time, manual
vs scheduled trigger, Pending/Running/Success/Failed status, start/end time,
duration, encrypted byte size, and safe failure details. A successful retained
archive can be **downloaded** from run details.

The existing portal Restore button is intentionally limited to legacy
host-mounted archives and always creates an **isolated new Compose deployment**
with explicit confirmation. Rule-managed Local/SMB archives can be downloaded
as ciphertext and restored using \`backup_cli.py restore\` on a trusted Docker
host with the protected age identity. **There is no in-place automatic
restore** and no browser-uploaded archive may run commands on the host.
Restore drills against a clean, separate host are still required before
claiming production disaster-recovery readiness. Restored databases contain
the backup configuration present at snapshot time; they are not overwritten
in the source deployment.

## Test and operations checklist

\`\`\`bash
python -m unittest tests.test_backup_rules -v
python -m unittest tests.test_backup_host_agent -v
python -m unittest tests.test_backup_locations -v
python -m unittest tests.test_backup_restore -v
python -m compileall -q .
node --check static/js/portal.js
\`\`\`

Tests use an in-memory SQLite database, temporary local directories, and
stubbed host socket/SMB/age operations. No production share, live Docker
snapshot, or real cron schedule is required. Deployment acceptance still
requires a real encrypted snapshot, a recovery drill on another host, and
testing destination disconnections, capacity exhaustion and large transfers.

## Security and migration notes

The management API requires the existing administrator authorization and CSRF
protection. The operator authenticates peer UIDs and accepts generated run
identities, not arbitrary host paths or shell commands. Retention never scans
or deletes unregistered files. The database stores only SMB Fernet ciphertext
and non-secret run metadata; error messages never interpolate network library
exceptions. Protect both the age identity and BACKUP_LOCATIONS_KEY separately
from backup archives.
