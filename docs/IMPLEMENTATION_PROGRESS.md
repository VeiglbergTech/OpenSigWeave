# Implementation progress

**Project:** OpenSigWeave standalone preview  
**Working branch:** `architecture/refactor-foundation`  
**Status:** First Docker-testable revision; not a production release  
**Last documentation review:** 2026-11-11

This document describes the current branch state. It is not a promise that deferred work is complete.

## 1. Implemented

### Application and identity

- FastAPI application with server-rendered portal and management APIs.
- Optional OIDC Authorization Code login using a configured discovery URL; local administrator login is available when `OIDC_ENABLED=false`.
- OIDC callback (registered only when enabled) validates subject, email, boolean `email_verified` and the `groups` array. Local-only mode uses a configured email and salted scrypt password hash.
- Portal authorization uses the exact group names `signature-users` and `signature-admins`.
- Authentik API-backed employee attribute lookup provides profile fields, groups, claims and custom attributes in OIDC mode; local-only mode returns empty external attributes.
- Authentik-specific logout and attribute lookup remain; a generic OIDC-only provider is not yet fully supported.

### Signature functionality

- Domain configuration and user-specific overrides.
- Signature catalog with global/domain scope, active state and immutable HTML versions.
- Hybrid persistence: PostgreSQL metadata and versioned HTML files with SHA-256 integrity validation.
- Signature variants with weights and per-sender selection state to avoid immediate repeats.
- Rule persistence and evaluation by priority, with AND-combined sender/domain/group/claim/attribute conditions.
- Modes: `AUTO`, `PROFESSIONAL`, `INTERNAL` and `NONE`.
- Server-side HTML sanitization and automatic escaping of dynamic profile values.

### Security baseline

- Startup validation for strong, non-placeholder `SECRET_KEY` and `ENGINE_API_KEY`.
- HTTPS validation for OIDC discovery and Authentik API configuration.
- TLS certificate verification and bounded Authentik API requests.
- Secure, HttpOnly, SameSite session cookie with a one-hour lifetime.
- Session-bound CSRF protection for state-changing browser requests.
- Trusted Host validation and response security headers.
- Global request-body limit and bounded signature HTML storage.
- Sandboxed signature previews and server-side HTML sanitization.
- Database-backed shared rate limiting; forwarded client IP headers are trusted only from configured proxy CIDRs.
- File-store path validation, symlink rejection, atomic writes and hash verification.
- GitHub Actions dependency audit, syntax checks, migrations, regression tests and full-history Gitleaks scan.

### Standalone Docker

- Python 3.12 application image.
- Non-root application UID/GID 10001.
- Read-only application container filesystem, bounded temporary filesystem, dropped Linux capabilities and `no-new-privileges`.
- PostgreSQL 17 Alpine service with healthcheck.
- Persistent named volumes for PostgreSQL and signature HTML.
- Application port bound to host loopback at `127.0.0.1:8085`.
- Startup waits for PostgreSQL, applies Alembic migrations and then starts Uvicorn.
- Application process healthcheck.
- Standalone `.env.example` and Docker Compose installation/upgrade documentation.

## 2. Verification completed

CI run #520 on commit `753deb6a8ac8ad2a893a396575c18b4e7eaa0a62` passed:

- Full-history Gitleaks scan.
- Python dependency audit.
- Python compilation, browser JavaScript syntax and Lua syntax checks.
- Alpine CSP evaluator smoke test.
- PostgreSQL and SQLite Alembic migrations.
- Docker image build.
- Compose configuration and stack startup.
- PostgreSQL readiness and application healthchecks.
- Alembic migration execution inside the application container.
- Authenticated signature-engine API request against the running PostgreSQL-backed application.
- All 102 regression tests.

The CI run proves the container build/startup and database/API smoke path and exercises local-only startup/authentication configuration. It does not prove successful interactive OIDC login, full browser behavior or end-to-end email delivery.

## 3. Current limitations

### Identity

- OIDC discovery and login are configurable, but the employee-attribute provider and logout URL are still Authentik-specific.
- Authentik UserInfo must provide a verified email and a groups array. The application currently uses `signature-users` and `signature-admins`.
- The Authentik API service token is used to retrieve active user profile attributes on every signature lookup. Technical lookup failures fall back to the last successful PostgreSQL cache; confirmed missing or inactive accounts clear that cache. If no cached record exists, profile attributes are empty.

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

- The Compose deployment has persistent volumes, but a tested automated backup/restore procedure is not yet provided.
- Rate-limit thresholds, especially the engine endpoint threshold, need validation against realistic mail throughput.
- A dynamic penetration test and realistic load test have not been performed.

## 4. Required before wider preview/release

1. Complete a real-browser test of login, permissions, signature editing, rules and logout.
2. Review browser CSP reports and fix actual violations; do not enable enforcement until verified.
3. Exercise backup and restore of both PostgreSQL and signature-file volumes.
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
