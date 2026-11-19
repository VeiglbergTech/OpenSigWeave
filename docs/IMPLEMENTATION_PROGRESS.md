# Implementation status

**Project:** OpenSigWeave standalone preview  
**Status:** Core application and automated preview validation are progressing; external acceptance remains. Not production-ready.  
**Development branch:** `dev` contains the experimental Postfix Milter adapter and its integration coverage. `beta` has not been promoted. `main` remains unchanged; PR #2 remains Draft and unmerged.

## Implemented

- **Application:** FastAPI portal and management API, PostgreSQL/Alembic persistence, SQLite compatibility and server-rendered UI.
- **Identity:** Per-domain login and profile providers; managed Authentik/generic OIDC, LDAP/Active Directory over LDAPS and independent local emergency administrator. Roles: `signature-users`, `signature-admins`.
- **Signatures:** Domain and personal overrides, catalog, immutable HTML versions, variants, weighted no-repeat selection, groups, GROUP rules and per-message `{{ group:slug }}` placeholders.
- **HTML:** Original source storage, server-side sanitization before use, autoescaped profile values and sandboxed previews. Outlook conditional comments/VML are not currently preserved by the sanitizer; see [HTML source preservation](HTML_SOURCE_PRESERVATION_ADR.md).
- **Administration:** Signature/group/rule management and selective ZIP export with additive, validated import and conflict handling.
- **Security:** Strong secret validation, CSRF, secure sessions, trusted-host checks, request limits, shared database-backed rate limiting, proxy-CIDR validation and symlink-safe atomic signature storage.
- **Backups:** Encrypted Docker-aware full backups, verification, fresh-deployment restore, verified retention and guarded systemd scheduling to mounted remote storage.
- **Deployment:** Python 3.12 non-root application container, read-only filesystem, dropped capabilities, PostgreSQL 17 and loopback-only application port.
- **Rspamd adapter:** Separate Lua integration using the versioned engine API. Top-level `multipart/*` messages are deliberately left unchanged.
- **Stalwart adapter:** Experimental native DATA-stage hook. It uses the shared engine service, parses MIME with Python's email package, preserves attachments/inline parts, skips signed/encrypted or malformed messages, prevents duplicate insertion and now places signatures before recognized quoted-reply boundaries. It is not live SMTP/DKIM certified.
- **Signature placement:** Shared Python helper for the Stalwart adapter supports common plain-text and HTML reply/forward boundaries. Rspamd retains its separate native Lua splitter; the two runtimes do not share one helper.
- **Postfix/Milter:** Experimental PyMilter 1.0.6 sidecar implemented in `adapters/postfix_milter.py`. Isolated CI runs the actual OpenSigWeave Milter class in disposable Postfix, verifies authenticated signature insertion in the final delivered MIME, unauthenticated no-op behavior and removal of client-supplied selector headers. The test uses a mock engine; live HTTPS/API integration, DKIM ordering and production deployment remain unverified.

Detailed contracts and operator instructions: [architecture](ARCHITECTURE.md), [API](API.md), [adapters](ADAPTERS.md), [backup/restore](BACKUP_RESTORE_DESIGN.md), [security review](WEB_AND_RSPAMD_REVIEW.md) and [signature groups](SIGNATURE_GROUPS_DESIGN.md).

## Automated verification

- Latest complete application CI: [run #44501075118](https://github.com/VeiglbergTech/OpenSigWeave/actions/runs/44501075118), commit `300e14b4174fd62c4286a06b9fd9c94c040fdebf`, result **success**. All workflow job steps completed successfully, including secret scan, dependency audit, Python compile/security checks, JavaScript and Lua syntax, PostgreSQL/SQLite migrations, Docker build/smoke test, encrypted backup/fresh restore and regression tests. This run also validates the README Buy-Me-a-Coffee badge image-source correction.
- Latest dedicated Postfix compatibility CI: [run #44309369390](https://github.com/VeiglbergTech/OpenSigWeave/actions/runs/44309369390), commit `9036d0f72a64c8f05b5b7e3abea66a7fd9c43f1f`, result **success**. It verifies PyMilter runtime, real Postfix SMTP/Milter exchange and final delivery through the actual OpenSigWeave adapter with a mock engine. The delivered message contains the inserted signature and idempotency marker; unauthenticated mail remains unchanged and control headers are removed.
- `beta` promotion is pending until the documentation refresh and its resulting CI checks complete; `main` remains unchanged.
- The Postfix compatibility spike is tracked separately in [POSTFIX_MILTER_DESIGN.md](POSTFIX_MILTER_DESIGN.md). Its protocol test is not equivalent to an authenticated sender authorization acceptance test.
- CI success does not establish production readiness or replace live mail-flow, identity-provider, email-client, restore or load tests.

## Outstanding acceptance and engineering work

| Area | Required verification |
|---|---|
| Identity | Live LDAP/Active Directory and Authentik sign-in, group mapping and logout |
| Browser/editor | Investigate Quill 1.3.7 `style-src-attr` CSP reports; keep CSP report-only until resolved. Verify real email-client rendering and Outlook behavior. |
| HTML fidelity | Define a security-reviewed policy for Outlook conditional comments/VML; current sanitizer removes them. |
| Backup | Execute the documented two-host restore drill and test real NFS/SMB mount, disconnect and recovery behavior. |
| Rspamd | In disposable Mailcow, verify sender authorization, supported non-multipart processing, idempotency, Rspamd order and final DKIM. Rotate the historical Rspamd key wherever deployed. |
| Stalwart | Run isolated live SMTP/DKIM tests for reply placement, multipart/alternative, attachments, inline images, retries, idempotency and failure behavior. |
| Postfix | Verify live engine HTTPS/API credentials and certificate trust; test signed/large/multipart messages, retry/idempotency, failure policy and final DKIM validation. |
| Operations/security | Validate rate limits against real throughput and perform dynamic security/load testing. Verify canonical Host and trusted forwarded-header behavior on the intended reverse-proxy topology. |

## Scope boundaries

- Keep OpenSigWeave independent of Mailcow; native Mailcow UI/authentication is a separate future project.
- Do not enable production mail flow until disposable SMTP/DKIM acceptance tests pass.
- Do not enable enforced CSP until real-browser reports have been resolved.
- PR #2 (`beta` → `main`) remains Draft. Do not merge it or update `main` until the owner confirms successful real-environment testing.

## Signature storage layout

The configured storage root contains `signatur/{Signaturname}/signature.html` and immutable UUID snapshots under `signatur/{Signaturname}/.versions/`. PostgreSQL retains stable internal IDs and metadata; renaming a signature renames its directory without changing its ID.
