#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

: "${PROJECT_DIR:?Set PROJECT_DIR in the backup environment file}"
: "${BACKUP_DIRECTORY:?Set BACKUP_DIRECTORY to the dedicated mounted backup target}"
: "${AGE_RECIPIENT:?Set AGE_RECIPIENT to the age recipient public key}"
: "${AGE_IDENTITY:?Set AGE_IDENTITY to the protected private identity file}"

RETENTION_COUNT="${RETENTION_COUNT:-7}"
DOCKER_BINARY="${DOCKER_BINARY:-docker}"
AGE_BINARY="${AGE_BINARY:-age}"
PYTHON_BINARY="${PYTHON_BINARY:-python3}"

for command in mountpoint findmnt flock date; do
    command -v "$command" >/dev/null 2>&1 || { echo "Required command missing: $command" >&2; exit 2; }
done
[[ "$RETENTION_COUNT" =~ ^[1-9][0-9]*$ ]] || { echo "RETENTION_COUNT must be a positive integer" >&2; exit 2; }
[[ -d "$PROJECT_DIR" && ! -L "$PROJECT_DIR" ]] || { echo "PROJECT_DIR must be a real directory" >&2; exit 2; }
[[ -f "$PROJECT_DIR/backup_cli.py" && ! -L "$PROJECT_DIR/backup_cli.py" ]] || { echo "backup_cli.py not found in PROJECT_DIR" >&2; exit 2; }
[[ -d "$BACKUP_DIRECTORY" && ! -L "$BACKUP_DIRECTORY" ]] || { echo "BACKUP_DIRECTORY must be a real directory" >&2; exit 2; }
mountpoint -q "$BACKUP_DIRECTORY" || { echo "Backup target is not mounted; refusing to write to local disk" >&2; exit 2; }
filesystem_type="$(findmnt -n -o FSTYPE --target "$BACKUP_DIRECTORY")"
BACKUP_FS_ALLOWLIST="${BACKUP_FS_ALLOWLIST:-nfs nfs4 cifs smb3 fuse.sshfs fuse.rclone}"
case " $BACKUP_FS_ALLOWLIST " in
    *" $filesystem_type "*) ;;
    *) echo "Backup target filesystem is not an approved remote type: $filesystem_type" >&2; exit 2 ;;
esac
[[ -f "$AGE_IDENTITY" && ! -L "$AGE_IDENTITY" ]] || { echo "AGE_IDENTITY is missing or unsafe" >&2; exit 2; }
command -v "$PYTHON_BINARY" >/dev/null 2>&1 || { echo "Python executable not found: $PYTHON_BINARY" >&2; exit 2; }
command -v "$DOCKER_BINARY" >/dev/null 2>&1 || { echo "Docker executable not found: $DOCKER_BINARY" >&2; exit 2; }
command -v "$AGE_BINARY" >/dev/null 2>&1 || { echo "age executable not found: $AGE_BINARY" >&2; exit 2; }

LOCK_FILE="${LOCK_FILE:-/run/opensigweave/backup.lock}"
LOCK_DIRECTORY="$(dirname -- "$LOCK_FILE")"
[[ -d "$LOCK_DIRECTORY" && ! -L "$LOCK_DIRECTORY" ]] || { echo "Backup lock directory is missing or unsafe" >&2; exit 2; }
[[ ! -L "$LOCK_FILE" ]] || { echo "Backup lock file must not be a symbolic link" >&2; exit 2; }
exec 9>>"$LOCK_FILE"
flock -n 9 || { echo "Another OpenSigWeave backup is already running" >&2; exit 0; }

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_path="$BACKUP_DIRECTORY/opensigweave-$stamp.age"

echo "Creating encrypted OpenSigWeave backup: $backup_path"
"$PYTHON_BINARY" "$PROJECT_DIR/backup_cli.py" create \
    --project-dir "$PROJECT_DIR" \
    --output "$backup_path" \
    --recipient "$AGE_RECIPIENT" \
    --identity "$AGE_IDENTITY" \
    --docker-binary "$DOCKER_BINARY" \
    --age-binary "$AGE_BINARY"

echo "Applying verified retention; keeping $RETENTION_COUNT backups"
"$PYTHON_BINARY" "$PROJECT_DIR/backup_cli.py" prune \
    --directory "$BACKUP_DIRECTORY" \
    --identity "$AGE_IDENTITY" \
    --keep "$RETENTION_COUNT" \
    --age-binary "$AGE_BINARY" \
    --apply

echo "OpenSigWeave backup and retention completed successfully"
