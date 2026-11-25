# Upgrade and recovery guide (2026.11-alpha early access)

> **Deployment automation:** The new `./install.sh`, `./generate_config.sh` and `./update.sh` workflows are described in [INSTALL_UPDATE.md](INSTALL_UPDATE.md) and in [OpenSigWeave-Docs](https://github.com/VeiglbergTech/OpenSigWeave-Docs/tree/dev/src/content/docs/installation). This older candidate checklist remains for manual verification and recovery.

**Status:** Candidate procedure, not a production acceptance certificate. Published release deployment path: standalone Docker Compose stack on manually approved `main`, with prebuilt GHCR application image. Unreleased `beta` remains the staging checkout. Do not promote into main during the candidate audit. Keep Mailcow/Rspamd integration disabled in production until the [adapter acceptance matrix](MAILCOW_RSPAMD_INTEGRATION.md) has passed.

## 1. Preflight and backup

1. Record the current Git commit (`git rev-parse HEAD`), Docker image ID (`docker image inspect`), deployment host, external URL and expected DNS/reverse-proxy routing. Record the running Mailcow/Rspamd version separately if that adapter is enabled.
2. Confirm free space for database and persistent files. Backups are optional for the updater; if a rule is enabled, its encrypted archive and offline recovery identity must be usable. For production deployments, independently verified recovery backups are strongly recommended.
3. Use `docker compose ps`, `docker compose logs --tail=100 opensigweave`, and the administrator **System** tab to check health and DB/storage status. Confirm an emergency local administrator can log in.
4. Check `.env` permissions (`chmod 600 .env`), `APP_ALLOWED_HOSTS`, independent `POSTGRES_PASSWORD`, `SECRET_KEY`, `ENGINE_API_KEY`, and the optional distinct `MANAGEMENT_API_KEY`. Provider credentials should be stored in the managed identity-provider volume, not committed to Git.
5. Prefer a tested **encrypted full backup** before production changes. `./update.sh` nevertheless permits installations with no configured Backup Rule. When a configured backup fails, it pauses for an explicit decision; `--yes` does not bypass failure.

```bash
cd /opt/opensigweave
git rev-parse HEAD
docker compose ps
python3 backup_cli.py create \
  --project-dir /opt/opensigweave \
  --output /backup/opensigweave-pre-v2026.11.age \
  --recipient 'age1REPLACE_WITH_YOUR_PUBLIC_AGE_RECIPIENT' \
  --identity /media/offline/age-identity.txt
python3 backup_cli.py verify /backup/opensigweave-pre-v2026.11.age \
  --identity /media/offline/age-identity.txt
```

The example recipient above is intentionally a **placeholder**, not a working encryption key. Create and safely store your own age key pair. The backup CLI requires Docker and `age` installed on the operator host. Read [backup/restore design](BACKUP_RESTORE_DESIGN.md), including consistency restrictions and retention.

**Preferred automatic path:** `./update.sh --check`, `--prefetch`,
`--dry-run`, then `./update.sh` (with `--approve-breaking` only after
review). Its backup decision and journal behavior are described in
[INSTALL_UPDATE.md](INSTALL_UPDATE.md). The manual `backup_cli.py create`
command below is a separate legacy full-archive workflow, not a prerequisite
for installing or running updates.

## 2. Breaking changes and data migration

- Legacy `AUTO`, `INTERNAL` and `PROFESSIONAL` signature modes are retired from normal application selection, current API and UI. Migration `0014_retire_signature_modes` converts legacy pool rules to ordinary signature groups; legacy DB columns/tables remain for compatibility. **Review all converted groups, rules and domain scoping after upgrading.** Never re-enable mode selection to work around a bad migration.
- Signatures are versioned. Restoring a historical version creates a *new* active version; it does not erase history. Existing `signatur/{Signaturname}/signature.html` paths and `.versions/` snapshots live on the persistent signatures volume.
- Managed Authentik/generic OIDC or LDAPS providers are configured in **Administration → Identity Providers**, with login and profile provider selected **per domain**. Legacy root-level OAuth/OIDC environment configuration is not the supported model. Callback URI is `https://<host>/auth/<provider-id>`.
- Strong non-placeholder secrets, an independently configured local administrator and an explicit host allowlist are required. HTTPS certificate validation must remain enabled.
- The versioned engine endpoint is `GET /api/v1/engine/signatures/{email}`. The old `GET /api/signature/{email}` endpoint remains an Rspamd compatibility alias; do not make new adapters depend on it.
- Signature image URLs require externally reachable anonymous `GET /signature-assets/` even when the portal itself uses SSO. Images are stored in the signature volume; branding overrides are stored in PostgreSQL.
- Concrete schedule windows are stored UTC with a half-open end boundary. Recheck local timezone assumptions, daylight-saving transitions and overlapping priorities.

**Migration warning:** Docker starts the application by running `alembic upgrade head`. Do not assume a database can be downgraded just by checking out an older commit or rebuilding an older image. Preserve your verified pre-upgrade backup; test a fresh-target restore first.

## 3. Candidate upgrade

After `beta` has received a completely green, approved commit, stop outbound signature injection in the mailserver or switch traffic to a disposable test environment for the upgrade window. Then:

```bash
cd /opt/opensigweave
git status --short
git fetch origin main
git checkout main
git pull --ff-only origin main
./update.sh --check
./update.sh --prefetch
./update.sh --dry-run
./update.sh
docker compose ps
docker compose logs --tail=100 opensigweave
docker compose exec -T opensigweave alembic current
```

Abort if the checkout is dirty or diverged, an enabled backup fails without explicit override, Compose validation fails, the healthcheck fails or migrations error. **Never** use `docker compose down --volumes` for an in-place upgrade.

## 4. Post-upgrade checks

Check the **System** tab, local emergency login, managed OIDC/LDAP sign-in and authorization, domain provider mapping, signatures/versions/restore-as-new, group rotation and no-repeat behavior, rules and priority, timezone-specific schedules, branding/logo/favicon persistence and historical editor images. Check a normal user cannot administer the application and the management API key is not confused with the engine key.

Only enable the staged Mailcow/Rspamd, Postfix or Stalwart adapter after its documented end-to-end sender, MIME, failure-policy and DKIM tests pass on the **specific deployment**. Confirm public image GET is accessible from outside the internal network without exposing administrator APIs.

## 5. Failure and recovery

1. Capture the failed Git SHA, `docker compose ps`, migration revision and redacted logs. Disable the signature adapter at the MTA if necessary rather than risking message corruption.
2. For a code-only error *without schema or data changes*, assess a controlled checkout of the recorded old commit; do not presume this is a safe data rollback.
3. For failed migrations, broken data, lost versions or persistent incompatibility, restore the **verified full encrypted backup into a fresh Compose target** (different project name, directory and volumes). The restore CLI intentionally refuses existing target resources. Validate the fresh instance **before** moving DNS/proxy traffic.
4. Keep the original deployment untouched for investigation. Restore instructions and a two-host drill are in [CROSS_HOST_RESTORE_DRILL.md](CROSS_HOST_RESTORE_DRILL.md). A same-host CI restore is not cross-host evidence.
5. Roll back a disposable Mailcow adapter independently using the removal/configuration procedure in [MAILCOW_RSPAMD_INTEGRATION.md](MAILCOW_RSPAMD_INTEGRATION.md), then recheck normal mail delivery and DKIM.

Do not promote to `main` or create a version tag until a maintainer has reviewed the [manual acceptance record](RELEASE_ACCEPTANCE_v2026.11.md) and explicitly authorized it. The [GHCR workflow](RELEASE_PROCESS.md) builds a precompiled image on an existing self-hosted runner after approval; published hosts pull the package and do not build it.
