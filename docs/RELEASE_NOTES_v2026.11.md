# OpenSigWeave 2026.11-alpha — prepared early-access notes

> **NOT RELEASED YET.** Planned: `2026.11-alpha`, then `2026.11-alpha1`, `2026.11-alpha2`, etc. This is **early access**, not v1.0 Stable. An approved main version tag triggers GHCR publishing only after exact-main CI gates pass. See [release process](RELEASE_PROCESS.md).

## Highlights

- Self-hosted FastAPI administration portal backed by PostgreSQL and Alembic migrations, including a limited read-only **System** diagnostics tab.
- Signature catalog with per-domain eligibility, immutable source versions, historical previews and non-destructive **restore-as-new-version**.
- CKEditor 5 editor with source editing, upload of PNG/JPEG/WebP images (GIF compatibility), persistent immutable public image URLs and shared historical image assets.
- Signature groups with weighted, no-repeat selection; nested `{{ group:slug }}` templates; prioritized sender/domain/group/claim/attribute rules; optional per-user overrides.
- Time-window scheduling for a signature or group with UTC persistence, domain precedence, priority and deterministic overlap rules.
- Managed Authentik/generic OIDC and LDAPS/Active Directory identity providers with per-domain login/profile assignment, plus independent local emergency administrator.
- Custom branding (application name, accent color, PNG logo, PNG/ICO favicon) persisted in PostgreSQL, with built-in asset reset.
- Administrator REST API and optional independent `MANAGEMENT_API_KEY`; local operator CLI for encrypted full backup, verification, fresh restore and retention.
- Adapter-neutral signature engine. Rspamd Lua adapter has parser-backed multipart regression coverage; Postfix PyMilter and Stalwart MTA hook are **experimental**, not production-certified.

## Breaking changes / migration considerations

- Fixed `AUTO`, `INTERNAL`, `PROFESSIONAL` signature modes have been retired from runtime/API/UI. Migration `0014_retire_signature_modes` converts legacy pool rules to signature groups; historical compatibility storage remains. Verify converted groups, their domain scoping, rules and rotation before enabling delivery.
- The supported authentication design uses managed providers and per-domain assignment. Legacy root-level OAuth/OIDC configuration and callback routes are not the supported path; reconfigure callbacks as `/auth/{provider-id}`.
- Deployment requires distinct strong secrets, explicit `APP_ALLOWED_HOSTS`, a local administrator password hash and HTTPS/LDAPS verification. Incorrect or placeholder configuration can prevent startup.
- The preferred versioned engine route is `/api/v1/engine/signatures/{email}`. The old `/api/signature/{email}` path remains as a compatibility alias.
- Scheduling is explicit date/time only; recurring schedules are not implemented. Ordinary mail-body marker checksums select **concrete** signatures, not retired modes.

## Upgrade

1. Read [UPGRADE.md](UPGRADE.md) and record the current commit and deployment topology.
2. Create and **verify** an encrypted full backup before changing code or applying migrations. Backups contain secrets. A browser ZIP export is **not** a full backup.
3. After a manually approved `main` version tag publishes the prebuilt image, use `./update.sh` to pull the source-matched GHCR image (no local Docker build), validate migrations, health and the administrator System tab.
4. Check identity-provider/group mappings, migrated rules/groups, signature-version restore, editor images, schedules, branding and external asset GET.
5. Enable any MTA integration only after real SMTP, MIME, sender-authorization and final DKIM acceptance. If migration fails, prefer a fresh-target restore from the verified backup; checking out old code alone is not a database rollback.

## Security-related improvements

- CSRF and role checks for state-changing administration; independent engine and optional administrator API credentials; strict host and secret validation.
- Hardened read-only non-root application container, dropped privileges, bounded request bodies, safe signature storage and immutable image assets.
- Server-side HTML sanitization and autoescaped identity values; guarded file upload validation; sensitive provider configuration stored outside tracked source.
- CI includes full-history secret scanning, pinned Python dependency audit, Bandit, Trivy filesystem/image scans, CodeQL, browser tests, database migrations, Docker smoke tests and encrypted backup/restore.
- **Caveat:** the browser Content Security Policy remains **Report-Only**; full HIGH/CRITICAL container image findings are reported, but the blocking Trivy policy only fails for vulnerabilities with an upstream fix. A green workflow is not a statement that the image has zero unfixed vulnerabilities.

## Known limitations / release gates

- Isolated Mailcow/SOGo tests confirmed signed mail, CID, nested replies, rules, schedules, rotation, Authentik, domain fallbacks and selected outage/recovery paths. Local `dkimpy` validation passed. These observations do not certify all aliases, SMTP pathways or external mail clients.
- Signature insertion is not guaranteed exactly once across independent SMTP resubmissions, changed/rotated signatures or other competing mail rewriters.
- Protected S/MIME/PGP inputs need separate acceptance. The Rspamd adapter retains backward-compatible `fail_open`, but optional `failure_policy=tempfail` rejected tested engine and Milter outages. The separate opt-in guard catches a missing injector **only when the guard itself is loaded**. Fully independent signing enforcement remains unverified.
- SOGo replies, MIME/CID and attachments worked in live testing. Outlook 2016 was deferred after TLS/transport issues in the temporary isolated environment. Thunderbird, external DKIM and unusual MIME cases remain unverified.
- Authentik was tested; LDAP/LDAPS was not. Cross-host disaster recovery, SMB outage, penetration tests, sustained load and full Content Security Policy enforcement are not certified.
- No recurring schedules, nested LDAP group expansion, SCIM provisioning, full CLI/Web parity, or Outlook VML delivery support.

**Release authority:** Documentation stays on `dev` until CI promotes it to `beta`; explicit maintainer approval is required for `main` and creation of any `YYYY.MM-alpha[N]`, beta or stable tag. The GHCR workflow reacts to approved tags but does not create them. Green CI alone is not authorization.
