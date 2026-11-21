# OpenSigWeave v2026.11 / v1.0 — prepared release notes

> **NOT RELEASED.** Candidate notes for review, not a published GitHub Release or tagged production version. Feature statements describe the `dev` candidate reviewed in November 2026. The final released commit must be recorded only after release approval and corresponding tests.

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
3. Move only to a fully tested and promoted `beta` commit with `git pull --ff-only`. Validate Compose, build, migrations, health and the administrator System tab.
4. Check identity-provider/group mappings, migrated rules/groups, signature-version restore, editor images, schedules, branding and external asset GET.
5. Enable any MTA integration only after real SMTP, MIME, sender-authorization and final DKIM acceptance. If migration fails, prefer a fresh-target restore from the verified backup; checking out old code alone is not a database rollback.

## Security-related improvements

- CSRF and role checks for state-changing administration; independent engine and optional administrator API credentials; strict host and secret validation.
- Hardened read-only non-root application container, dropped privileges, bounded request bodies, safe signature storage and immutable image assets.
- Server-side HTML sanitization and autoescaped identity values; guarded file upload validation; sensitive provider configuration stored outside tracked source.
- CI includes full-history secret scanning, pinned Python dependency audit, Bandit, Trivy filesystem/image scans, CodeQL, browser tests, database migrations, Docker smoke tests and encrypted backup/restore.
- **Caveat:** the browser Content Security Policy remains **Report-Only**; full HIGH/CRITICAL container image findings are reported, but the blocking Trivy policy only fails for vulnerabilities with an upstream fix. A green workflow is not a statement that the image has zero unfixed vulnerabilities.

## Known limitations / release gates

- No adapter is yet production-certified. Live Mailcow/Rspamd SMTP/DKIM chain and authenticated sender/alias authorization are still unverified; the dedicated Postfix integration CI uses a mock engine, not live production credentials.
- Signature insertion is not guaranteed exactly once across independent SMTP resubmissions, changed/rotated signatures or other competing mail rewriters.
- S/MIME/PGP signed/encrypted and unsupported/malformed MIME inputs are deliberately skipped. Rspamd currently fails open on signature-engine errors: delivery can proceed without a signature.
- Reply/forward placement is heuristic; real SOGo, Outlook and Thunderbird versions, inline CIDs, attachments, charsets and final DKIM must be tested.
- No live Authentik/LDAPS acceptance, cross-host disaster-recovery drill, real NFS/SMB outage test, dynamic penetration test, sustained load test or all-browser CSP enforcement has been certified.
- No recurring schedules, nested LDAP group expansion, SCIM provisioning, full CLI/Web parity, or Outlook VML delivery support.

**Release authority:** These notes may be prepared or edited on `dev`. Promotion to `main`, creation of a `v1.0` tag or publication of a GitHub Release requires an explicit, separate maintainer approval after the [release acceptance checklist](RELEASE_ACCEPTANCE_v2026.11.md) has been completed. No approval is implied by green GitHub Actions.
