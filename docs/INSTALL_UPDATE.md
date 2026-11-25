# Installation, configuration and safe channel-bound updates

> Published alpha installations use a manually approved `main` version tag and pull a prebuilt GitHub Container Registry image. `dev`/`beta` remain source-testing channels; this does not certify production use.

## Host requirements

Linux x86_64/aarch64, Docker Engine >= 24, Docker Compose v2 >= 2.20,
Git, Python 3, Docker daemon access, at least 2 GiB RAM and 6 GiB free space.
The scripts do not install Docker, modify the firewall or create remote accounts.
The checkout must be a clean, non-shallow clone from the approved Git origin.

## First installation: no manual key creation

```bash
git clone --branch main https://github.com/VeiglbergTech/OpenSigWeave.git
cd OpenSigWeave
./install.sh
```

The wizard proposes the host FQDN (or `localhost`), a local administrator
email, **allowed portal hostnames** (`APP_ALLOWED_HOSTS`) and
**trusted reverse proxy IPs/CIDRs** (`TRUSTED_PROXY_CIDRS`). Hosts are a
comma-separated allowlist without `https://`, ports, or wildcards.
Trusted proxies are optional and default to none; configure **only the
immediately connected reverse proxy IP(s) or narrow networks**, never `0.0.0.0/0`
or `::/0`. The wizard also asks for `APP_BIND_ADDRESS` (default `0.0.0.0`,
all IPv4 interfaces) and picks the first available port in 8085–8105 on
that interface. It asks for a hidden administrator password and confirmation;
the saved value is a salted scrypt
hash, not plaintext. Non-interactive installation requires an explicit
`--generate-admin-password` option; this creates a **0600** local file
`.admin-recovery-credentials` with the one-time initial password. Move this
file to a protected offline password manager promptly, then delete its local
copy. It is ignored by Git.

```bash
./install.sh --hostname signatures.example.com --admin-email admin@example.com
# Headless first install (explicit opt-in to generated admin password):
./install.sh --generate-admin-password --hostname signatures.example.com \
  --allowed-hosts "signatures.example.com,localhost" \
  --trusted-proxy-cidrs "192.0.2.8/32"
# First-time configuration without starting the stack:
./generate_config.sh --hostname signatures.example.com
```

Installation generates independent `POSTGRES_PASSWORD`, `SECRET_KEY`,
`ENGINE_API_KEY` and Fernet-compatible `BACKUP_LOCATIONS_KEY`. It writes
a protected `.env` with atomic create, validates Compose, starts and checks
PostgreSQL, **pulls and SHA-verifies the GHCR app image on `main`**, starts it with no local build, applies Alembic on startup and performs a
final healthcheck. Docker creates the persistent PostgreSQL, signature,
identity-provider and backup named volumes. No backup location, encryption
identity, OIDC, LDAP, Stalwart or other optional integration is required.

**Security:** by default `APP_BIND_ADDRESS=0.0.0.0` listens on **all IPv4
interfaces**. **Do not publicly expose the management port.** Restrict access
with a host firewall to your HTTPS reverse proxy, or enter
`127.0.0.1` at the interactive bind-address prompt for a same-host reverse proxy. If your proxy
runs on a separate host, binding to a specific private-interface IP is
safer than binding to every interface; also limit the port to the proxy IP. `TRUSTED_PROXY_CIDRS` controls which immediate peers Uvicorn
trusts for forwarded headers; it does **not** change the listening address,
open a firewall, or authorize arbitrary `Host` headers. The allowed hostname
list is enforced by the application. TLS must terminate at a trusted reverse
proxy; do not expose plaintext management endpoints to the internet.
Both `./install.sh` and `./generate_config.sh` ask for the bind address
**interactively during first-time configuration**, with `0.0.0.0` as the
default. Neither script accepts a `--bind-address` option. Noninteractive
installation retains the default. An existing `.env` is never silently
overwritten: change `APP_BIND_ADDRESS` in the protected file
deliberately, then recreate the affected application container for the new
port binding to take effect. Existing proxy/host allowlists are preserved
during reinstall and update.

## Existing deployments and configuration

```bash
./generate_config.sh          # first creation, or safe idempotent migration
./generate_config.sh --check  # read-only validation
./generate_config.sh --preview
./generate_config.sh --migrate
./install.sh                  # resume partial deployment or report healthy
```

Repeated execution preserves working passwords, scrypt hashes, URLs,
identity-provider credentials, and existing configuration values. Missing
safe defaults and `BACKUP_LOCATIONS_KEY` are appended atomically with mode
0600; **no key is rotated**. Missing existing PostgreSQL or essential
application secrets are not invented, because a replacement might make
existing databases or encrypted credentials unusable. Restore missing original
credentials from secure records. Never commit `.env` to Git.

## Backup behavior: entirely optional

The update manager queries **enabled Backup Rules** through the running
application container. The following behavior applies:

| Situation | Update behavior |
| --- | --- |
| No enabled Backup Rules | Continue normally; no backup warning or failure |
| Enabled usable rule | Create encrypted, complete application backup using that rule's Local/SMB location; re-read, decrypt, verify manifest and run `pg_restore --list` against the extracted DB dump |
| Enabled rule, but disabled/missing destination or invalid configuration | Treat as a failed configured backup, not an absent backup |
| Backup, transfer or verification failure | Stop before source changes; ask `Backup failed. Continue updating without a backup? [y/N]` |
| Answer `y` | Proceed, journal `failed_and_overridden` and display that no verified recovery point exists |
| Answer `N`, empty, Ctrl+C, EOF or unattended run without override | Abort with exit code 7; do not change the checkout, services or data |
| Unattended explicit override | Only `./update.sh --yes --continue-without-backup` permits continuing after failure |

Configured preference: `PREUPDATE_BACKUP_RULE_ID` in the application's
protected `.env` selects one **enabled** rule by ID. Without it, select the
first usable enabled rule sorted by case-insensitive rule name and then
stable ID. Unusable enabled rules are skipped only when another usable rule
exists; if none is usable, the configured backup fails and requires explicit
administrator approval before updating. A preferred rule that is missing,
disabled or invalid fails closed rather than falling back silently. Existing schedules, retention and locations are not
changed; the pre-update run is recorded in regular Backup History.

**Lazy age generation:** When a real backup is requested and no legacy age
environment values are provided, the container generates a new age identity
using `age-keygen` once, writes it as mode 0600 beneath the persistent
`/app/backups/.recovery/` directory (0700), and derives its public
recipient. No secret is displayed or committed. Export the recovery private
key to a separate protected destination after your first backup:

```bash
mkdir -m 700 -p /media/offline/opensigweave-recovery
./export_backup_key.sh /media/offline/opensigweave-recovery/age-identity.txt
```

The target file must not exist. Keep the key on different storage, outside
the OpenSigWeave deployment; losing the volume and the only recovery identity
makes saved ciphertext unusable. The protected `.env`, exact source/Compose
configuration and encrypted backup archive also need separate disaster-
recovery escrow. Existing explicit `BACKUP_AGE_RECIPIENT` with exactly one
`BACKUP_AGE_IDENTITY` or `BACKUP_AGE_IDENTITY_FILE` remains supported;
incomplete legacy configuration fails instead of generating a mismatched key.

## Updates

```bash
./update.sh --version
./update.sh --check
./update.sh --prefetch
./update.sh --dry-run
./update.sh
./update.sh --yes
# Only after reviewing critical source/schema changes:
./update.sh --yes --approve-breaking
# Unattended explicit acceptance of backup failures:
./update.sh --yes --continue-without-backup
```

`--check`, `--prefetch` and `--dry-run` do not require or create backups.
`--check` is a read-only remote comparison. `--prefetch` fetches Git
objects but does not change the checked-out source, services or data.
`--dry-run` validates the current deployment without mutation. The actual
update checks Git fast-forward ancestry, existing Docker/Compose health,
PostgreSQL and Alembic, persistent named volumes, and critical paths (which
require independent `--approve-breaking`). It does not auto-switch branches.

On `main`, the updater first pulls the GHCR image and verifies its `org.opencontainers.image.revision` label against the **exact Git target commit**. If the package is not published or the tag points to the wrong SHA, the update stops **before changing source, volumes or schema**. `OSW_IMAGE_TAG` defaults to `alpha`, or can be pinned to an immutable version such as `2026.11-alpha1` (update a pin intentionally before upgrading). Only dev/beta staging uses a local build overlay. See [release process](RELEASE_PROCESS.md).

After the backup decision, the updater writes a protected 0600 journal under
`.git/opensigweave-update-state.json` with structured `backup.status`:
`not_configured`, `verified`, or `failed_and_overridden`. Successful
records include Backup Rule, Location, run ID and stored archive filename.
No imaginary backup path is printed. Only then does it fast-forward the
source, migrate safe `.env` settings, validate Compose, **use the pre-pulled GHCR image on `main` (no Docker build)**,
stop the application for a one-off Alembic migration, restart it and perform
health checks. Named volumes are never removed.

On an interrupted image update, migration or restart, the journal remains and
blocks further update checks until the operator reconciles actual state.
**A failed migration never initiates a code-only or database rollback.**
Without a verified backup, no automatic restoration point exists. Even with
a verified application-profile backup, recovery needs a **fresh isolated
target**, the original encrypted archive, identity, source, protected
configuration and manual database/file restore as described in
[BACKUP_RULE_MANAGEMENT.md](BACKUP_RULE_MANAGEMENT.md).

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success or no update |
| 2 | Parser/configuration error |
| 3 | Subprocess failure |
| 4 | Unsupported host/Git/configuration or cancelled operation |
| 5 | Concurrent installation/update |
| 6 | Unhealthy runtime, volume or database schema |
| 7 | Configured backup failed and wasn't explicitly overridden |
| 8 | Interrupted/failed migration or startup; manual recovery required |

## Acceptance and limitations

```bash
bash -n install.sh generate_config.sh update.sh export_backup_key.sh
python3 -m unittest discover -s tests -p test_deployment_manager.py -v
python3 -m unittest discover -s tests -p test_backup_preupdate.py -v
python3 -m unittest discover -s tests -p test_backup_engine.py -v
docker compose config --quiet
```

The in-container backup **does not capture Docker images or the host `.env`**;
it stores PostgreSQL, signatures and identity-provider configuration only.
Manifest checks, ciphertext readback and `pg_restore --list` validate
artifact structure, but are **not proof** of a full cross-host production
restore. Real SMB/NAS failure testing, cold restore and live mail-server
integration remain separate release acceptance steps. `dev` is development,
`beta` is tested code and `main` is manually approved. The [self-hosted GHCR publishing workflow](RELEASE_PROCESS.md) runs only after a valid explicitly approved version tag is pushed on main; no additional Git branches are introduced.
