# Backup management in the administration portal

The **Backups** administrator tab controls the existing full, age-encrypted
Docker Compose backup system. It is **not** the selective Exports/Imports feature.
PostgreSQL (including rules, schedules, branding and version history), signature
files, identity-provider configuration, saved container images and deployment
configuration are all included in full backups.

## Security boundary

The application container receives **neither** the Docker socket nor the age
private identity. A separate root-owned host operator receives narrowly scoped
requests through a local Unix socket at
/run/opensigweave-backup/control.sock (mode 0600, 10001:root).

- The socket is owned by application UID 10001 and mode 0600; the host-side parent directory remains root:root mode 0700. Docker bind-mounts the socket file into the container, so host group access is not required. SO_PEERCRED independently allows only root and the configured application UID. Check that BACKUP_APP_UID matches the running container user.
- FastAPI requires administrator authorization; browser mutations use CSRF.
- No shell commands, arbitrary restore paths or Compose project names come from
  the browser; backup filenames are strictly validated.
- Backup directories must be remote mountpoints of approved filesystem types
  beneath BACKUP_ALLOWED_PARENT (default /mnt); missing mounts fail closed.
- Restore always creates a **new** Compose project and local bind port under
  BACKUP_RESTORE_ROOT (default /var/lib/opensigweave-restores).
- Age identities remain on the host. Keep an offline copy and test on a
  separate host before relying on a backup for disaster recovery.

This agent has privileged Docker access. Install it on trusted hosts only.
Never expose its Unix socket through a TCP proxy.

## Host setup

These commands assume an installation in /opt/opensigweave and a remote mount in
/mnt/opensigweave-backups. Review the fixed paths in the systemd units first.

1. Follow [the full backup design](BACKUP_RESTORE_DESIGN.md) to create an
   age key pair, mount a remote backup filesystem and create
   /etc/opensigweave/backup.env (owned by root, mode 0600). Store AGE_IDENTITY
   outside the backup mount and keep a separate offline copy.
2. Install existing service/timer plus the operator and service override:

       sudo install -m 0644 scripts/systemd/opensigweave-backup.service /etc/systemd/system/
       sudo install -m 0644 scripts/systemd/opensigweave-backup.timer /etc/systemd/system/
       sudo install -m 0644 scripts/systemd/opensigweave-backup-operator.service /etc/systemd/system/
       sudo install -d -m 0755 /etc/systemd/system/opensigweave-backup.service.d
       sudo install -m 0644 scripts/systemd/opensigweave-backup-ui-service-override.conf /etc/systemd/system/opensigweave-backup.service.d/portal.conf
       sudo systemctl daemon-reload
       sudo systemctl enable --now opensigweave-backup-operator.service

3. If the old timer is enabled, run this once before configuring the schedule
   from the portal:

       sudo systemctl disable --now opensigweave-backup.timer

4. Add the following entry to the application's host .env to include the
   optional Compose socket mount also for future updates:

       COMPOSE_FILE=compose.yaml:compose.backup-control.yaml

   Recreate the application after the host operator is running:

       docker compose up -d --no-deps --force-recreate opensigweave

5. Open Administration → Backups. Set a mounted directory, interval in hours,
   maximum retained copies and the automatic-backup toggle. Saving writes
   protected /etc/opensigweave/backup-ui.json, /etc/opensigweave/backup-ui.env
   and a systemd timer override. The existing host backup script and CLI remain
   the source of truth for snapshot, encryption, verification and retention.

The portal reports **unavailable** until the operator and remote mount are
ready. It will never silently fall back to a local disk.

## Actions

**Create** queues a consistent, encrypted backup on the host. The existing
snapshot briefly stops and restarts the application container. The portal may
disconnect during this operation; refresh when the source is running again.

**Download** streams the encrypted age archive, never decrypted plaintext.

**Delete** asks for confirmation. The host checks archive names and file types
and refuses to delete while another operation is running.

**Restore** requires an additional confirmation. It calls the existing
backup_cli.py fresh-deployment restore with an isolated project, directory and
localhost port. The live deployment is never overwritten. The recovered copy
contains the original credentials: inspect it before manually changing
production routing. All recovery actions should be accompanied by an offline,
cross-host restore drill.

The latest job state is held in the host operator process. If it restarts,
inspect systemd logs and actual artifacts: a lost status does **not** prove
backup success.

## Testing

Run unit tests with:

    python -m unittest discover -s tests -v

The existing Docker/age integration test:

    bash tests/docker_backup_restore_e2e.sh

now restores alongside the running source to a different localhost port and
verifies the original and recovered HTTP listeners, PostgreSQL records,
signature files and identity-provider credentials. It remains a same-host
test, not a substitute for a real two-host disaster-recovery drill.


## Backup Locations (Local and SMB)

The **Backup Locations** section is an independent PostgreSQL-backed registry
for storage destinations. Administrators can add, edit, enable/disable, test
and remove locations. The interface uses the same modal and confirmation
patterns as Domain Management. No new scheduler is introduced.

- **Local** uses a location-specific, immutable UUID directory under
  \`/app/backups/\` (or \`BACKUP_LOCAL_ROOT\`). Docker Compose persists the
  \`opensigweave-backups\` named volume. The application owns the directory,
  mode 0700, and location names never form filesystem paths. Deleting a
  location deliberately leaves its data intact for manual recovery.
- **SMB** uses direct SMB3 (mandatory signing and encryption) over TCP/445,
  with server/share/relative directory, username, password and optional
  domain. It never calls mount or a shell. The connection test verifies both
  access and write permissions with a temporary probe file.
- SMB credentials are Fernet-encrypted in PostgreSQL using an independent
  \`BACKUP_LOCATIONS_KEY\` from the protected application \`.env\`. Generate one
  with \`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"\`
  and keep an offline copy. The API never returns passwords or ciphertext;
  leaving the password blank while editing preserves the old encrypted value.
  If the key is missing, SMB writes/tests fail with a configuration error.
- Endpoints: \`GET/POST /api/admin/backups/locations\`,
  \`PUT/DELETE /api/admin/backups/locations/{id}\`,
  and \`POST /api/admin/backups/locations/{id}/test\`.
  They require administrator access; mutations require CSRF protection.

**Important integration boundary:** the existing age-encrypted full backup,
host-side \`/etc/opensigweave/backup.env\`, host operator, legacy scheduling,
download, retention and isolated restore remain unchanged. Creating a Location
does **not** automatically redirect current host-operator backups: that operator
still requires its approved, mounted remote destination. The registry is ready
for a subsequent Backup Rules execution integration; this change does not
create a second scheduler, open the Docker socket in the app, or expose age keys.
The new Local volume is not automatically included inside the full snapshot
(to avoid recursively backing up backup archives). Test recovery procedures and
keep off-host copies before relying on it.

For running installations, apply \`alembic upgrade head\` and recreate the
application container to attach the new persistent backup volume. After
deploying, configure \`BACKUP_LOCATIONS_KEY\` once and protect its recovery copy.

## Backup Rules and Backup History

For the new rule-managed full encrypted backups, UTC scheduling, Local/SMB
publication, per-rule retention, run history, migration 0017, and restore
limitations, see [Backup Rule Management](BACKUP_RULE_MANAGEMENT.md).
These features require the updated host operator, its protected age configuration
and a persistent local backup volume. Legacy remote-mount backup controls remain
available independently.
