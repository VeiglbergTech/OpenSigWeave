# Implementation status

**Project:** OpenSigWeave standalone preview  
**Status:** Automated preview validation passed; external acceptance remains. Not production-ready.  
**Verified baseline:** `f5f197c3b1c0740527f7e307525cd5fe1db11932`

## Implemented

- **Application:** FastAPI portal and management API, PostgreSQL/Alembic persistence, SQLite compatibility and server-rendered UI.
- **Identity:** Per-domain login and profile providers; managed Authentik/generic OIDC, LDAP/Active Directory over LDAPS and independent local emergency administrator. Roles: `signature-users`, `signature-admins`.
- **Signatures:** Domain and personal overrides, catalog, immutable HTML versions, variants, weighted no-repeat selection, groups, GROUP rules and per-message `{{ group:slug }}` placeholders.
- **HTML:** Original source storage, server-side sanitization before use, autoescaped profile values and sandboxed previews. Outlook conditional comments/VML are not currently preserved by the sanitizer; see [HTML source preservation](HTML_SOURCE_PRESERVATION_ADR.md).
- **Administration:** Signature/group/rule management and selective ZIP export with additive, validated import and conflict handling.
- **Security:** Strong secret validation, CSRF, secure sessions, trusted-host checks, request limits, shared database-backed rate limiting, proxy-CIDR validation and symlink-safe atomic signature storage.
- **Backups:** Encrypted Docker-aware full backups, verification, fresh-deployment restore, verified retention and guarded systemd scheduling to mounted remote storage.
- **Deployment:** Python 3.12 non-root application container, read-only filesystem, dropped capabilities, PostgreSQL 17 and loopback-only application port.
- **Mail adapter:** Separate Rspamd Lua integration. Top-level `multipart/*` messages are deliberately left unchanged.

Detailed contracts and operator instructions: [architecture](ARCHITECTURE.md), [API](API.md), [backup/restore](BACKUP_RESTORE_DESIGN.md), [security review](WEB_AND_RSPAMD_REVIEW.md) and [signature groups](SIGNATURE_GROUPS_DESIGN.md).

## Automated verification

- Full regression baseline: push #1822 and PR #1823 passed **244 tests** on implementation commit `e49f8ce6322224cb7feda48764fe4810d2e0701f`.
- Latest pre-cleanup CI: push #1843 and PR #1844 passed on README-only commit `f5f197c3b1c0740527f7e307525cd5fe1db11932`.
- CI covers full-history Gitleaks scanning, dependency audit, Python/JavaScript/Lua syntax, Alpine CSP evaluator, PostgreSQL and SQLite migrations, Docker build/startup/health, Chromium/Playwright browser workflows, scheduled-backup guards, encrypted backup/fresh restore and the Python regression suite.
- PostgreSQL 17 tests include 12-worker concurrency checks for variant and group selection. Browser coverage includes local login, authorization boundaries, signature/group/rule CRUD, export/import preview, domain override policy, logout and Quill editor synchronization.

The later changes through the verified baseline were documentation-only. Passing CI does not establish production readiness or replace live integration tests.

## Outstanding external acceptance

| Area | Required verification |
|---|---|
| Identity | Live LDAP/Active Directory and Authentik sign-in, group mapping and logout |
| Browser/editor | Investigate two Quill 1.3.7 `style-src-attr` CSP reports; keep CSP report-only until resolved. Verify real email-client rendering and Outlook behavior. |
| HTML fidelity | Define a security-reviewed policy for Outlook conditional comments/VML; current sanitizer removes them. |
| Backup | Execute the documented two-host restore drill and test real NFS/SMB mount, disconnect and recovery behavior. |
| Mail flow | In disposable Mailcow, verify sender authorization, supported non-multipart processing, idempotency, Rspamd order and final DKIM. |
| Operations/security | Rotate the historical Rspamd key wherever deployed; validate rate limits against real throughput and perform dynamic security/load testing. |
| Reverse proxy | Verify canonical Host and trusted forwarded-header behavior on the intended deployment topology. |

## Scope boundaries

- Keep OpenSigWeave independent of Mailcow; native Mailcow UI/authentication is a separate future project.
- Do not enable production mail flow until the disposable SMTP/DKIM acceptance tests pass.
- Do not enable enforced CSP until real-browser reports have been resolved.
- The current pull request remains Draft; do not merge or deploy to production without explicit approval.

## Signature storage layout

The configured storage root contains `signatur/{Signaturname}/signature.html` and immutable UUID snapshots under `signatur/{Signaturname}/.versions/`. PostgreSQL retains stable internal IDs and metadata; renaming a signature renames its directory without changing its ID.
