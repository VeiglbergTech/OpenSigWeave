# OpenSigWeave host-side installation and updates

These tools are implemented for review on `dev`. They are **not** a production acceptance certificate and do not publish a release.

## Host requirements

Linux x86_64/aarch64, Docker Engine >= 24, Docker Compose v2 >= 2.20, Git, Python 3, Docker daemon access, at least 2 GiB RAM and 6 GiB free local space. They never install system packages or open firewall ports. The service is mailserver-independent.

## Installation and configuration

```bash
git clone --branch dev https://github.com/VeiglbergTech/OpenSigWeave.git
cd OpenSigWeave
./install.sh
# Or separately, before installation:
./generate_config.sh
./generate_config.sh --check
./generate_config.sh --preview
./generate_config.sh --migrate
```

The installer only works in the checked-out branch and never changes a remote branch. For `beta` or `main`, explicitly clone that branch. A local admin email, public hostname, private bind address and host port are prompted. The password is accepted through getpass and transformed into the application's existing salted scrypt format. The PostgreSQL password, SECRET_KEY and ENGINE_API_KEY are independently generated. `.env` is mode 0600, never automatically overwritten, and remains ignored by Git.

`--preview` and `--migrate` can add only missing, nonsecret defaults; newly required secrets must be populated by the administrator, not guessed. The application container always listens on 8085; `APP_PORT` changes only the host mapping. Use HTTPS on a trusted reverse proxy.

## Secure updates

```bash
./update.sh --version
./update.sh --check
./update.sh --prefetch
./update.sh --dry-run
./update.sh
./update.sh --skip-start
./update.sh --yes --approve-breaking   # only after explicit review
```

`--check` is read-only, using `git ls-remote`, and may not know the target's details until `--prefetch` downloads the commit to a remote-tracking ref. `--dry-run` performs read-only runtime preflight. Actual updates require a clean, non-shallow Git checkout, authorized upstream, fast-forward ancestry, healthy PostgreSQL/application with correct volumes, and a verified encrypted age backup **before any source or app changes**.

Create a private operator file (0600) and provide its path through `OSW_BACKUP_CONFIG`. The default path is `/etc/opensigweave/backup.env`.

```dotenv
BACKUP_DIRECTORY=/mnt/opensigweave-backups
AGE_RECIPIENT=age1REPLACE_WITH_REAL_PUBLIC_RECIPIENT
AGE_IDENTITY=/media/offline/age-identity.txt
```

The backup directory must be writable and on a mounted remote NFS, SMB, SSHFS or rclone filesystem. The private age identity must have mode 0600, remain outside Git and `.env`, and be available during the update. `age` and `findmnt` must be installed. The updater calls the existing `backup_cli.py create` (including verification) and `backup_cli.py verify` again, then performs a local `git merge --ff-only`, validates Compose/config, builds the image, stops the application, performs one-off Alembic migration and starts and healthchecks the service. `--skip-start` leaves the application stopped after migration.

Critical adapter, Docker/Compose, environment, backup or schema changes require a separate `--approve-breaking` option, even with `--yes`. No automatic reset, downgrade or full restore is attempted. If migration or post-migration startup fails, **do not use an older application against the new schema**: use the verified backup and `backup_cli.py restore` to a fresh target as described in [BACKUP_RESTORE_DESIGN.md](BACKUP_RESTORE_DESIGN.md).

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success or no update |
| 2 | Configuration/parser error |
| 3 | Subprocess or command failure |
| 4 | Unsupported environment, Git state, configuration or cancelled operation |
| 5 | Concurrent deployment lock held |
| 6 | Unhealthy runtime, migration version or missing volume |
| 7 | Missing/unsafe backup or failed backup verification |
| 8 | Migration/startup failure requiring explicit recovery |

## Known limits for acceptance testing

The `main` channel builds locally; there is no automatic prebuilt GHCR pull or provenance attestation yet. Version comparison uses exact Git object IDs, not lexicographic tags; prior-to-prefetch remote release notes may be unavailable. Signed Git commit verification is not enforced. The update manager is not an unattended remote monitor or a scheduler. Integration with active external Rspamd/Postfix/Stalwart servers must be validated separately. Dedicated host-level tests for real remote backup mounts, an interrupted upgrade, and a physical cross-host restore are still required before production approval. Existing Compose deployments continue to work without these scripts.

## Testing

Run `bash -n install.sh generate_config.sh update.sh`, `shellcheck` on the wrappers, and `python3 -m unittest discover -s tests -p test_deployment_manager.py -v`. The existing CI also exercises encrypted Docker backup and fresh restore; it is *not* proof of a successful live production update.

Public canonical docs: [OpenSigWeave-Docs](https://github.com/VeiglbergTech/OpenSigWeave-Docs/tree/dev/src/content/docs/installation).
