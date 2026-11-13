#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
SOURCE="$WORK/opensigweave-ci-source"
RESTORE="$WORK/restored"
SOURCE_PROJECT="opensigweave-ci-source"
RESTORE_PROJECT="opensigweave-ci-restore"
IDENTITY="$WORK/age.identity"
BACKUP="$WORK/full-backup.age"

cleanup() {
  if [[ -f "$RESTORE/compose.yaml" && -f "$RESTORE/compose.restore.yaml" ]]; then
    docker compose --project-name "$RESTORE_PROJECT" \
      --file "$RESTORE/compose.yaml" \
      --file "$RESTORE/compose.restore.yaml" down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi
  if [[ -f "$SOURCE/compose.yaml" ]]; then
    docker compose --project-name "$SOURCE_PROJECT" \
      --file "$SOURCE/compose.yaml" down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

command -v age >/dev/null
command -v age-keygen >/dev/null
[[ -f "$ROOT/.env" ]] || { echo "The Docker smoke-test environment file is missing" >&2; exit 1; }

mkdir -p "$SOURCE"
git -C "$ROOT" archive HEAD | tar -x -C "$SOURCE"
cp "$ROOT/.env" "$SOURCE/.env"
chmod 600 "$SOURCE/.env"

age-keygen -o "$IDENTITY" >/dev/null 2>&1
chmod 600 "$IDENTITY"
RECIPIENT="$(age-keygen -y "$IDENTITY")"

docker compose --project-name "$SOURCE_PROJECT" --file "$SOURCE/compose.yaml" up -d --build --wait --wait-timeout 180

docker compose --project-name "$SOURCE_PROJECT" --file "$SOURCE/compose.yaml" exec -T postgres sh -c \
  'exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<'SQL'
CREATE TABLE backup_restore_probe (
  id integer PRIMARY KEY,
  payload text NOT NULL
);
INSERT INTO backup_restore_probe (id, payload) VALUES (1, 'roundtrip-ok');
SQL

docker compose --project-name "$SOURCE_PROJECT" --file "$SOURCE/compose.yaml" exec -T opensigweave sh -c \
  'mkdir -p /data/signatures /app/.idp && printf "%s" "<p>restore-e2e</p>" > /data/signatures/restore-probe.html && printf "%s" "idp-volume-roundtrip" > /app/.idp/restore-probe.txt'

python "$SOURCE/backup_cli.py" create \
  --project-dir "$SOURCE" \
  --output "$BACKUP" \
  --recipient "$RECIPIENT" \
  --identity "$IDENTITY"

# The source deployment is disposable. Stop and remove it to free its port and
# prove that the restore does not depend on any source containers or volumes.
docker compose --project-name "$SOURCE_PROJECT" --file "$SOURCE/compose.yaml" down --volumes --remove-orphans

python "$SOURCE/backup_cli.py" restore "$BACKUP" \
  --identity "$IDENTITY" \
  --target-dir "$RESTORE" \
  --project-name "$RESTORE_PROJECT" \
  --health-timeout 240

RESTORE_COMPOSE=(docker compose --project-name "$RESTORE_PROJECT" --file "$RESTORE/compose.yaml" --file "$RESTORE/compose.restore.yaml")
db_value="$("${RESTORE_COMPOSE[@]}" exec -T postgres sh -c \
  'psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT payload FROM backup_restore_probe WHERE id = 1"')"
[[ "$db_value" == "roundtrip-ok" ]] || { echo "Restored PostgreSQL probe does not match" >&2; exit 1; }

signature_value="$("${RESTORE_COMPOSE[@]}" exec -T opensigweave cat /data/signatures/restore-probe.html)"
[[ "$signature_value" == "<p>restore-e2e</p>" ]] || { echo "Restored signature volume does not match" >&2; exit 1; }

idp_value="$("${RESTORE_COMPOSE[@]}" exec -T opensigweave cat /app/.idp/restore-probe.txt)"
[[ "$idp_value" == "idp-volume-roundtrip" ]] || { echo "Restored IDP volume does not match" >&2; exit 1; }

echo "Full encrypted backup/restore integration passed: PostgreSQL, Alembic revision, signature volume, IDP volume and application health."
