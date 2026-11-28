# Installation, configuration and safe channel-bound updates

> Published alpha installations use a manually approved `main` version tag and pull a prebuilt GitHub Container Registry image. `dev`/`beta` remain source-testing channels; this does not certify production use.

## Host requirements

Linux x86_64/aarch64, Docker Engine >= 24, Docker Compose v2 >= 2.20,
Python 3, Docker daemon access, at least 2 GiB RAM and 6 GiB free space.
Git is required **only for source-checkout installations** (development, beta,
or older main installations). A verified, published **minimal main release
bundle** can run and update without Git. The scripts do not install Docker,
modify the firewall or create remote accounts. Source installations must
remain clean, non-shallow checkouts of the approved Git origin.

For the minimal package format, authenticated download and release-asset
verification, see [Minimal main release installation](MINIMAL_RELEASE_INSTALL.md).

## First installation: no manual key creation

When the matching installation asset has been attached to an approved GitHub
Release, prefer the verified minimal package instructions in
[MINIMAL_RELEASE_INSTALL.md](MINIMAL_RELEASE_INSTALL.md). **Older releases do
not necessarily have such an asset**; the Git-based instructions below remain
supported for those releases and for source-based development.


**Terminal block 1:**

```bash
git clone --branch main https://github.com/VeiglbergTech/OpenSigWeave.git && cd OpenSigWeave && ./install.sh
```

The installer asks for hostname, allowed hosts, trusted reverse proxies and
bind address. It creates independent database, engine and application secrets.
The main checkout is aligned to the **latest published main release Git tag**
before the first database deployment. The exact GHCR version is pinned in the
protected local `.env` and its embedded revision is compared to Git.

Fresh installations default to **browser administrator setup**. On the host,
read the one-time token in `.admin-setup-token` (file permissions `0600`).
Use `https://<hostname>/setup` via a restricted HTTPS reverse proxy;
enter the code, administrator email and a strong password. The code only works
while no web administrator exists. Remove the token file after setup. Never
publish the token, store it in version control or expose the management port
without protection.

After login open **Admin → System** to select your mail-server adapter and
display the minimum needed server configuration. Any further required terminal
blocks count toward the **four-block installation limit**. Selecting a server
is not itself proof of a functioning mail flow. Finish with an authenticated
test message and verify the inserted signature before going live.

Existing installations retain their local .env administrator login and
secrets. The legacy `--admin-email` or `--generate-admin-password`
installer options remain available if a console-provisioned account is
specifically required. Default CLI password provisioning is deprecated.

Installation does not install Docker, create DNS records, change the firewall,
configure HTTPS, or automatically edit a remote mail server. For other mail
servers see [adapter documentation](ADAPTERS.md).

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

## Updates — one terminal block

**Stable/main release channel:**

```bash
./update.sh
```

This resolves the newest published main GitHub Release, including an alpha
or beta prerelease if no newer stable release has been published, verifies its
Git tag/commit against the approved main history, then pulls and verifies
the matching GHCR image before changing source or database. It records the
immutable version tag in `.env`, preserving local secrets.

**Latest beta development channel:**

```bash
./update.sh --beta
```

This fetches the latest beta **commit**, checks out the existing or new
`beta` branch without rewriting divergent history and builds the source
locally using the Compose build overlay. The `main` release is never built
locally.

When the beta commit is unchanged, the updater verifies both the Git-revision
label on the local image and whether the running container uses that exact
image. If all checks pass, `--beta` exits without creating another backup,
building or restarting. Older unlabeled images are rebuilt once.

The Compose overlay uses `pull_policy: never` to prevent Alembic's one-off
containers from rebuilding the image after the explicit update build. Alembic
current/head output is parsed by revision, not by the first token of potentially
mixed BuildKit output. Parsing failures stop the upgrade rather than reporting
a false successful migration.

### One-time transition from a legacy alpha updater

An older `update.sh` may not recognize `--beta`. Bootstrap once using
the authorized remote Git branch, then run the new updater for the full
rebuild and migration. Only fast-forward existing local history; never discard
local changes or use `docker compose down -v`.

~~~bash
cd /opt/OpenSigWeave
git fetch origin beta
git switch beta
git merge --ff-only origin/beta
./update.sh --beta
~~~

`git switch beta` normally creates a local tracking branch automatically
when only `origin/beta` exists. If it does not, use
`git switch --track -c beta origin/beta` instead.

The updater **must rebuild beta even when Git already matches origin/beta**,
because the running app container may still use the old alpha image. The
preflight, backup decision, migrations and health checks must still run.

Optional diagnostic flags remain `--check`, `--dry-run`, `--prefetch`
and `--version`; `--yes` supports noninteractive execution. The ordinary
update no longer requires separately copying an `--approve-breaking`
command. Critical files are still highlighted; migrations, data-volume,
backup-selection and post-start checks remain mandatory.

**Limits of "always works":** Updates must be idempotent on a healthy,
compatible, clean checkout. They must **not** falsely succeed after network
outages, failed image validation, diverged Git histories, interrupted
migrations or missing privileges. For safety they stop without automatic
history rewrites or schema rollback, and explain required recovery. Do not
silently downgrade a newer beta schema to an older main release.

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
