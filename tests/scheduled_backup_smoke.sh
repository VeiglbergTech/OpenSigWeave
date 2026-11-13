#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
BIN="$WORK/bin"
mkdir -p "$BIN" "$WORK/project" "$WORK/remote"
: > "$WORK/project/backup_cli.py"
: > "$WORK/project/compose.yaml"
: > "$WORK/project/.env"
: > "$WORK/age-identity.txt"
chmod 600 "$WORK/age-identity.txt"

cat > "$BIN/mountpoint" <<'STUB'
#!/usr/bin/env bash
[[ "${MOUNTED:-yes}" == yes ]]
STUB
cat > "$BIN/findmnt" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "${BACKUP_FS_TYPE:-nfs4}"
STUB
cat > "$BIN/docker" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
cat > "$BIN/age" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
cat > "$BIN/python3" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CALL_LOG"
if [[ "${1:-}" == "$PROJECT_DIR/backup_cli.py" && "${2:-}" == create ]]; then
  while (($#)); do
    if [[ "$1" == --output ]]; then
      shift
      : > "$1"
      break
    fi
    shift
  done
fi
STUB
chmod +x "$BIN"/*

export PATH="$BIN:$PATH"
export PROJECT_DIR="$WORK/project"
export BACKUP_DIRECTORY="$WORK/remote"
export AGE_RECIPIENT="age1testrecipient"
export AGE_IDENTITY="$WORK/age-identity.txt"
export RETENTION_COUNT=3
export LOCK_FILE="$WORK/backup.lock"
export CALL_LOG="$WORK/calls.log"
export PYTHON_BINARY=python3
export DOCKER_BINARY=docker
export AGE_BINARY=age
SCRIPT="$ROOT/scripts/backup_to_mounted_storage.sh"

# An absent mount must fail closed before backup creation or pruning.
if MOUNTED=no "$SCRIPT" >/dev/null 2>&1; then
  echo "Expected an unmounted backup target to be rejected" >&2
  exit 1
fi
[[ ! -s "$CALL_LOG" ]] || { echo "Backup CLI ran while the target was unmounted" >&2; exit 1; }

# A mounted local filesystem must also be rejected.
if BACKUP_FS_TYPE=ext4 "$SCRIPT" >/dev/null 2>&1; then
  echo "Expected a local filesystem to be rejected" >&2
  exit 1
fi
[[ ! -s "$CALL_LOG" ]] || { echo "Backup CLI ran on a disallowed filesystem" >&2; exit 1; }

# An approved remote filesystem runs create first and verified prune second.
BACKUP_FS_TYPE=nfs4 "$SCRIPT"
[[ "$(wc -l < "$CALL_LOG")" -eq 2 ]] || { echo "Expected create and prune invocations" >&2; exit 1; }
grep -q 'backup_cli.py create' "$CALL_LOG"
grep -q 'backup_cli.py prune' "$CALL_LOG"
backup_file="$(find "$WORK/remote" -maxdepth 1 -type f -name 'opensigweave-*.age' -print -quit)"
[[ -n "$backup_file" ]] || { echo "Backup command did not publish its expected output" >&2; exit 1; }

echo "Scheduled backup guard smoke test passed: unmounted and local targets refused; approved remote target creates backup then runs retention."
