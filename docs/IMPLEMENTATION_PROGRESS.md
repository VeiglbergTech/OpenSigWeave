# Implementation progress

**Project:** OpenSigWeave standalone preview  
**Working branch:** `architecture/refactor-foundation`  
**Status:** First Docker-testable revision; not a production release  
**Last documentation review:** 2026-11-14

This document describes the current branch state. It is not a promise that deferred work is complete.

## 1. Implemented

### Application and identity

- FastAPI application with server-rendered portal and management APIs.
- Managed identity providers are stored separately in `.idp/{provider-id}/.env`; secrets are not returned by management APIs.
- Domains have separate profile-attribute and login-provider assignments. Login is selected by the email domain.
- Authentik supports per-provider OIDC Authorization Code login with a provider-specific callback at `/auth/{provider-id}`; discovery, client ID, client secret, scopes and redirect URI are stored in that provider's file.
- LDAP / Active Directory supports LDAPS user authentication: a read-only service bind searches for an exact email match, then a second bind validates the submitted password as the user's DN. TLS certificate validation is mandatory and disabled AD accounts are rejected.
- The local administrator remains an independent emergency login when configured. New installations default to `OIDC_ENABLED=false`; the previous global OIDC configuration remains an opt-in compatibility path.
- OIDC callbacks validate subject, email, boolean `email_verified`, groups and the domain's current provider assignment.
- Portal authorization uses the exact group names `signature-users` and `signature-admins`.
- The profile provider is independent from the login provider and supplies name, phone, title, groups, claims and custom attributes to the portal and signature engine. Successful identity attributes are cached per provider and normalized email.
- First administrator visit opens identity-provider setup automatically when no provider exists.
- The login page renders a separate “Anmelden mit {display_name}” box for each enabled and fully configured Authentik OIDC provider. LDAP and local administrator access remain on the email/password form.
- Managed OIDC logout discovers the provider's HTTPS end-session endpoint and supports an optional post-logout redirect URI; legacy global OIDC logout remains Authentik-specific.

### Signature functionality

- Domain configuration and user-specific overrides.
- Signature catalog with global/domain scope, active state and immutable HTML versions.
- Hybrid persistence: PostgreSQL metadata and versioned HTML files with SHA-256 integrity validation.
- Signature variants with weights and per-sender selection state to avoid immediate repeats.
- Backup archive primitives with versioned manifests, SHA-256 inventory, bounded archive verification and safe extraction.
- age-based encryption/decryption for archives and standalone secret files.
- Docker Compose full-backup creation: service/volume preflight, application quiescence, PostgreSQL custom-format dump, recorded Alembic revision, signature and IDP volume snapshots, encrypted host `.env`, exact application/PostgreSQL image export, archive encryption and decrypt-and-verify.
- Staging-directory packaging, encrypted archive verification and decryption CLI commands.
- Verified retention CLI: newest N encrypted backups are retained; every candidate is decrypted and integrity-checked before any deletion; dry-run is the default and deletion requires `--apply`.
- Scheduled backup automation: systemd service and daily timer create full encrypted backups on a separately mounted remote filesystem, enforce a remote-filesystem allowlist, prevent overlapping runs and apply verified retention. A CI smoke test now verifies that unmounted targets and local filesystems are rejected before the backup CLI runs, that an approved remote filesystem runs backup creation followed by retention, and that symlinked lock files are refused without modifying their targets. The systemd unit creates a private `/run/opensigweave` runtime directory for the lock.
- Fresh-deployment restore: verifies the encrypted archive before creating target data, refuses existing Compose projects/volumes/directories, restores exact application image and PostgreSQL dump into new volumes, restores encrypted configuration, checks the restored Alembic revision and compares SHA-256 inventories of both restored data volumes against the backup manifest. Existing deployments are never overwritten.
- Selective administrator exports are implemented: administrators can download selected signatures and all HTML versions, domains and original templates, groups, rules and identity-provider metadata as a versioned ZIP with SHA-256 asset checksums. Exports are capped at 20 MiB, rate-limited, and exclude provider credentials, application secrets, personal overrides and identity caches. Rule conditions may contain identifiers; the UI warns before sharing. Import/reconciliation is not included.
- Cross-host restore drill, destructive in-place restore and real NFS/SMB validation remain outstanding. Scheduled remote-mount backup, verified retention, systemd service/timer, mountpoint enforcement and filesystem allowlisting are implemented; a real NFS/SMB host test is still required.
- Rule persistence and evaluation by priority, with AND-combined sender/domain/group/claim/attribute conditions.
- Modes: `AUTO`, `PROFESSIONAL`, `INTERNAL` and `NONE`.
- Server-side HTML sanitization and automatic escaping of dynamic profile values.
- Original signature HTML is preserved separately from sanitized delivery content for domain templates, personal/admin overrides and versioned catalog signatures. Migration `0012_html_source` backfills legacy values; all preview/mail rendering paths sanitize before output. Outlook conditional comments/VML warnings are present in all three raw-HTML editors. Automated source round-trip, API, sanitizer and migration tests pass; real-browser and email-client acceptance remain open.
- Editor replacement evaluated: retain vendored Quill 1.3.7 until a self-hosted CKEditor 5 GPL proof of concept passes email-HTML, sanitizer, Outlook, CSP/offline and licensing acceptance tests; see [EDITOR_DECISION.md](EDITOR_DECISION.md).
- Signature groups implemented on the development branch: administrator CRUD and membership UI/API, weighted no-repeat transactional selection, `GROUP` rule actions, stable Authentik `pk` / Active Directory `objectGUID` principal IDs, per-message `{{ group:slug }}` placeholders, deterministic administrator group previews in the group manager UI/API that do not mutate rotation state, and migrations that seed legacy groups without rewriting existing rules. See [SIGNATURE_GROUPS_DESIGN.md](SIGNATURE_GROUPS_DESIGN.md). The PostgreSQL concurrency regression now ran successfully against the CI PostgreSQL 17 service: both 12-worker stress tests (variant rotation and group rotation) passed on 2026-11-14. Full browser UI tests remain outstanding.

### Security baseline

- Startup validation for strong, non-placeholder `SECRET_KEY` and `ENGINE_API_KEY`.
- HTTPS validation for OIDC discovery and Authentik API configuration.
- TLS certificate verification and bounded Authentik API requests.
- Secure, HttpOnly, SameSite session cookie with a one-hour lifetime.
- Session-bound CSRF protection for state-changing browser requests.
- Trusted Host validation and response security headers.
- Global request-body limit and bounded signature HTML storage.
- Sandboxed signature previews and server-side HTML sanitization.
- Database-backed shared rate limiting for login, legacy and managed OIDC callbacks, and signature-engine requests; forwarded client IP headers are trusted only from configured proxy CIDRs. Managed `/auth/{provider_id}` callbacks are explicitly covered by the same callback policy as legacy `/auth`.
- File-store path validation, symlink rejection, atomic writes and hash verification.
- GitHub Actions dependency audit, syntax checks, migrations, regression tests and full-history Gitleaks scan.

### Standalone Docker

- Python 3.12 application image.
- Non-root application UID/GID 10001.
- Read-only application container filesystem, bounded temporary filesystem, dropped Linux capabilities and `no-new-privileges`.
- PostgreSQL 17 Alpine service with healthcheck.
- Persistent named volumes for PostgreSQL, signature HTML and managed identity-provider credentials.
- Application port bound to host loopback at `127.0.0.1:8085`.
- Startup waits for PostgreSQL, applies Alembic migrations and then starts Uvicorn.
- Application process healthcheck.
- Standalone `.env.example` and Docker Compose installation/upgrade documentation.

## 2. Verification completed

CI run `43230589615` on commit `f407333ffe86489f4409810fa65b503b3c0d7b91` passed:

- Full-history Gitleaks scan and Python dependency audit.
- Python compilation, browser JavaScript syntax and Lua syntax checks.
- Alpine CSP evaluator smoke test.
- PostgreSQL and SQLite Alembic migrations.
- Docker image build, Compose configuration and stack startup.
- PostgreSQL readiness, application healthchecks and Alembic migration execution.
- Authenticated signature-engine API request against the running PostgreSQL-backed application.
- All 146 regression tests, including LDAP bind authentication, managed OIDC discovery validation, domain-based LDAP/OIDC routing, separate OIDC provider buttons, callback validation and managed OIDC logout URL construction.

The CI run validates the code paths with test doubles and a local-only Docker stack. It does not prove successful interactive login or logout against a live Authentik or Active Directory server, full browser behavior or end-to-end email delivery.

The latest full CI runs `43594227040` (push) and `43594231897` (pull request) both passed on commit `be316f7e2b998617968c3edc51b9b80a2fff1143`. The PostgreSQL 17-backed regression suite ran 230 tests; both concurrent no-repeat stress tests passed. The suite now also exercises authorization through actual FastAPI HTTP routes with signed browser sessions: anonymous requests are denied, regular users can access only their personal signature endpoint, and administrators can read the protected management APIs. CI includes full-history secret scanning, dependency audit, Python/JavaScript/Lua checks, Alpine CSP smoke test, PostgreSQL and SQLite migrations, Docker build and smoke test, scheduled-backup remote-filesystem guard tests (including symlinked lock-file rejection), encrypted Docker backup and fresh restore, verified retention, and all 230 regression tests. It also covers selective administrator export archives, ZIP response headers, original HTML preservation, asset checksums and a separate export rate-limit policy. Both runs completed every step successfully. The backup/restore integration remains a same-host CI exercise, not a cross-host disaster-recovery drill.

## 3. Current limitations

### Identity

- Live interactive LDAP and OIDC sign-in still need verification against a real Active Directory and Authentik instance.
- Managed OIDC logout is implemented through discovery metadata, but the RP-initiated logout flow still needs live verification against Authentik.
- Authentik UserInfo must provide a verified email and a groups array. The application currently uses `signature-users` and `signature-admins`.
- The Authentik API service token and LDAP service bind retrieve profile attributes on signature lookup. Technical lookup failures fall back to the last successful PostgreSQL cache; confirmed missing or inactive accounts clear that cache. If no cached record exists, profile attributes are empty.

### Browser and CSP

- The frontend CSP remains `Content-Security-Policy-Report-Only`.
- Alpine's CSP-compatible build is version-pinned and protected by SRI, but still loaded from a CDN.
- jsdom CSP smoke tests exist; a real-browser walkthrough and review of actual CSP reports/Quill style behavior remain outstanding.

### Rspamd and mail flow

- The Lua adapter uses an HTTPS API endpoint, a shared engine key, bounded request timeout and response-size limits.
- Every top-level `multipart/*` message is deliberately left unchanged. The current rewrite path is limited to non-multipart messages.
- Outlook marker behavior needs tests with real client versions.
- SMTP envelope sender authorization must be enforced by Mailcow/Postfix. `task:get_user()` alone does not prove send-as authorization.
- Rspamd processing order and DKIM signing order have not been verified in a disposable Mailcow environment.
- No production Mailcow configuration has been changed.

### Operations

- Docker-aware full backup and initial fresh-deployment restore are implemented. Restore validates the database migration revision and the SHA-256 inventory of the signature and IDP volumes. Verified retention and a scheduled systemd backup to a separately mounted remote filesystem are implemented. A real cross-host restore drill, destructive in-place restore and real NFS/SMB validation remain outstanding; selective administrator exports are implemented. See [BACKUP_RESTORE_DESIGN.md](BACKUP_RESTORE_DESIGN.md).
- Historical CI failures involving archive inventory and migration compatibility have been corrected and superseded by the successful current full CI runs listed above.
- Rate-limit thresholds, especially the engine endpoint threshold, need validation against realistic mail throughput.
- A dynamic penetration test and realistic load test have not been performed.

## 4. Required before wider preview/release

1. Complete a real-browser test of login, permissions, signature editing, rules and logout.
2. Review browser CSP reports and fix actual violations; do not enable enforcement until verified.
3. Complete a documented cross-host restore drill covering PostgreSQL, the recorded Alembic revision, signature files, IDP credentials and deployment configuration. Validate the scheduled workflow against real NFS/SMB storage. Only after that consider destructive in-place restore. Selective administrator exports are already implemented.
4. Test Rspamd only in a disposable environment, including sender authorization, MIME fixtures, idempotency and DKIM validation.
5. Rotate the historical Rspamd engine credential anywhere it may have been deployed. Its old value remains in Git history; history has not been rewritten.
6. Validate rate-limit thresholds and run a focused security review.

## 5. Scope decisions

- Finish and test OpenSigWeave as a standalone application first.
- Keep the core independent of Mailcow-specific UI and account management.
- Do not implement native Mailcow UI integration in this repository. That will be a separate future project.
- Keep the current pull request as Draft until the standalone preview has been tested.
- Never deploy to production Mailcow as part of this development work without explicit approval.


### Signature file layout

The canonical HTML file is `signatur/{Signaturname}/signature.html`; immutable UUID-based version snapshots live under `signatur/{Signaturname}/.versions/`. The database ID remains stable when the signature name and directory are changed.
