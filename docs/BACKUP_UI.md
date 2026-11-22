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
/run/opensigweave-backup/control.sock (mode 0660, root:10001).

- Socket peer credentials allow only root and the application UID (10001).
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
