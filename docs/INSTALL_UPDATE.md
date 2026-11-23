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

`--check` is read-only, using `git ls-remote`, and may not know the target's details until `--prefetch` downloads the commit to a remote-tracking ref. `--dry-run` performs read-only runtime preflight. Actual updates require a clean, non-shallow Git checkout, authorized upstream, fast-forward ancestry, healthy PostgreSQL/application with correct volumes, and a verified encrypted application-profile age backup **before any source or app changes**.

The update manager now creates an encrypted pre-update **application-profile**
backup inside the `opensigweave-backups` Docker named volume, without running
a host backup operator, mounting a host NFS/SMB directory, or requiring host
`age`/`findmnt`. Configure `BACKUP_AGE_RECIPIENT` plus exactly one of
`BACKUP_AGE_IDENTITY` or `BACKUP_AGE_IDENTITY_FILE` in the protected
application `.env`. The container must have the configured identity available.
The updater runs:

```bash
docker compose exec -T opensigweave python backup_cli.py create-application --filename opensigweave-before-update-<UTC>-<sha>.age
docker compose exec -T opensigweave python backup_cli.py verify-application --filename opensigweave-before-update-<UTC>-<sha>.age
```

The filename above is illustrative; the updater generates the concrete UTC
timestamp and current Git SHA. The application snapshot includes PostgreSQL,
signatures and identity-provider files, but **does not include Docker image
exports or the protected host `.env`**. Off-host escrow of the image/source,
deployment config, age key and resulting archive remains mandatory for full
disaster recovery. A previous installation's app image might not yet know
these new CLI commands: first take a verified **legacy full backup**, then
perform a controlled upgrade and verify a new application backup. See
[BACKUP_RULE_MANAGEMENT.md](BACKUP_RULE_MANAGEMENT.md) and
[BACKUP_RESTORE_DESIGN.md](BACKUP_RESTORE_DESIGN.md).

The updater then performs `git merge --ff-only`, Compose/config validation,
image build, application stop, one-off Alembic migration, service start and
healthchecks. `--skip-start` leaves the application stopped after migration.

Critical adapter, Docker/Compose, environment, backup or schema changes require a separate `--approve-breaking` option, even with `--yes`. No automatic reset, downgrade or full restore is attempted. After a verified backup and before changing the checkout, an update journal is written to `.git/opensigweave-update-state.json` (0600). In case of an interrupted or failed source/build/migration/startup step, the journal remains and **all further update commands, including `--check`, fail with exit code 8** instead of claiming the installation is current. Inspect the recorded stage and backup; only remove the journal after an administrator has safely reconciled deployment state. Completed updates and deliberate `--skip-start` runs clear the journal. If migration or post-migration startup fails, **do not use an older application against the new schema**: use the verified backup and the appropriate fresh-target recovery procedure as described in [BACKUP_RESTORE_DESIGN.md](BACKUP_RESTORE_DESIGN.md).

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

The `main` channel builds locally; there is no automatic prebuilt GHCR pull or provenance attestation yet. Version comparison uses exact Git object IDs, not lexicographic tags; prior-to-prefetch remote release notes may be unavailable. Signed Git commit verification is not enforced. The update manager is not an unattended remote monitor or a scheduler. Integration with active external Rspamd/Postfix/Stalwart servers must be validated separately. Real SMB transfer tests, an interrupted upgrade, and a physical cross-host restore are still required before production approval. Existing Compose deployments continue to work without these scripts.

## Testing

Run `bash -n install.sh generate_config.sh update.sh`, `shellcheck` on the wrappers, and `python3 -m unittest discover -s tests -p test_deployment_manager.py -v`. The existing CI also exercises encrypted Docker backup and fresh restore; it is *not* proof of a successful live production update.

Public canonical docs: [OpenSigWeave-Docs](https://github.com/VeiglbergTech/OpenSigWeave-Docs/tree/dev/src/content/docs/installation).
