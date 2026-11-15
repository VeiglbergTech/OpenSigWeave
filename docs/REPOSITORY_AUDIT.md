# Repository and security audit

**Repository:** `VeiglbergTech/OpenSigWeave`  
**Reviewed branch:** `architecture/refactor-foundation`  
**Review date:** 2026-11-15  
**Purpose:** Engineering review of the current standalone preview. This is not a security certification or penetration test.

## 1. Scope and baseline

The original repository was a compact FastAPI application with an Authentik-specific profile lookup, SQLite persistence and a Rspamd Lua adapter. The current branch has been refactored substantially. The old findings below are retained as historical context only; the status table describes the current branch.

## 2. Current implementation summary

- FastAPI application with managed OIDC providers, LDAPS / Active Directory authentication and an independent local emergency administrator login; group-based authorization is shared across modes.
- Per-provider OIDC discovery/callback/logout handling and a separate profile-attribute provider.
- PostgreSQL and Alembic for standalone persistence; SQLite compatibility remains.
- Hybrid signature storage: relational metadata plus versioned HTML files.
- Signature catalog, variants, weighted selection and persisted rules.
- Server-side HTML sanitization, autoescaped profile variables and sandboxed previews.
- Session-bound CSRF, secure session cookies, trusted-host checks, response headers and body-size limits.
- Shared database-backed rate limiting for login, legacy and managed OIDC callbacks, and signature-engine requests.
- Versioned, encrypted Docker-aware full backups, fresh-deployment restore, verified retention and guarded scheduled backups to a mounted remote filesystem.
- Rspamd adapter with external configuration, HTTPS-only API URL, shared engine key, bounded timeout and response-size limits.
- Standalone Docker Compose stack with a non-root application container and persistent volumes.

## 3. Security findings and current status

| Area | Current status | Remaining risk/work |
|---|---|---|
| Historical engine credential | Removed from current Lua source; runtime configuration is external. | Treat the historical value as compromised and rotate wherever deployed. Git history was not rewritten. |
| Browser authorization | Admin and personal-user routes enforce group checks server-side; Chromium/Playwright covers signed normal-user sessions, administrator API access/denial, personal signature access and domain override allow/deny behavior. | Live LDAP/OIDC sign-in and reverse-proxy deployment behavior remain external acceptance work. |
| CSRF | Session-bound token required for state-changing browser requests; admin UI mutation paths are exercised in the browser smoke flow. | Validate behind the intended reverse-proxy topology and during live-provider sign-in. |
| Session security | Strong startup secret validation; Secure, HttpOnly, SameSite cookie with limited lifetime. | Validate behind the intended reverse-proxy topology. |
| Authentication | Managed OIDC providers, LDAPS / Active Directory bind authentication and independent local emergency administrator login; authorization is group-based. | Live sign-in and logout still need verification against real identity providers. |
| Signature-group concurrency | PostgreSQL 17 CI passed 12-worker concurrency tests for both variant and group no-repeat rotation; Chromium/Playwright covers group CRUD, membership/weight updates and GROUP rules. | Realistic production-load testing remains outstanding. |
| Authentik API | HTTPS required, TLS verification enabled, bounded timeout, exact email match and active-user check. | Validate least-privilege service account permissions in a real Authentik instance. |
| HTML handling | Server-side allowlist sanitizer, re-sanitization before use, autoescaped identity data and sandboxed previews. | Real-browser testing and email-client rendering compatibility remain. |
| Host validation | TrustedHostMiddleware accepts the callback hostname and explicit aliases only. | Proxy Host/forwarded-header behavior must match deployment topology. |
| Request size | Global request-body limit and bounded signature HTML storage. | Validate operational limits with representative usage. |
| Rate limiting | Atomic database-backed counters shared across workers; login, legacy and managed OIDC callback paths, and signature-engine requests are covered; proxy IP headers trusted only from configured CIDRs. | Tune thresholds against real engine throughput. |
| File storage | Generated UUID names, path containment, symlink checks, atomic writes and SHA-256 integrity verification. | Real cross-host restore and remote filesystem failure scenarios remain. |
| Administrator exports/imports | Selective versioned ZIP export and additive import are implemented, with SHA-256 asset checksums, archive/path/size validation, dry-run preview, conflict handling, administrator authorization and transactional cleanup. | Review rule conditions for identifiers before sharing; external operational acceptance remains. |
| Docker isolation | Non-root UID, read-only root filesystem, dropped capabilities, no-new-privileges and loopback-only port. | Container image vulnerability scanning and runtime deployment review remain useful follow-up work. |
| Rspamd MIME rewrite | Top-level multipart messages are deliberately left unchanged; only supported non-multipart rewrite paths are active. | No full Mailcow MIME/SMTP/DKIM integration proof. |
| CSP | Restrictive policy remains report-only; Alpine CSP build and SRI are used. Chromium reports two Quill 1.3.7 `style-src-attr` violations and no script-policy violations. | Editor strategy is deferred; resolve the actual violations before enforcement. |

## 4. Mail-flow trust boundaries

The Rspamd adapter obtains the envelope sender from the Rspamd task and requires an authenticated SMTP user. These are separate facts. The authenticated SMTP identity is not proof that the mailbox may send as the selected envelope sender.

Mailcow/Postfix sender verification must remain enabled. Mailbox aliases and delegated send-as identities must be configured explicitly. Do not add broad sender restrictions exceptions or global send-as permissions to make signature selection work.

The Rspamd module currently leaves every top-level `multipart/*` message unchanged. This is intentional fail-safe behavior. Do not enable raw string-based multipart reconstruction as a workaround.

The Lua prefilter registration does not prove DKIM order. A disposable integration test must inspect the effective Rspamd pipeline and cryptographically verify the final delivered DKIM signature.

## 5. Historical baseline findings

The initial audit on `main` identified:

- A static Rspamd engine credential in the Lua source.
- Missing route-level authorization on some domain-management endpoints.
- Weak fallback session-secret behavior.
- Disabled TLS certificate verification for Authentik API requests.
- Missing explicit CSRF protection.
- Untrusted HTML flowing into browser/editor paths.
- Manual MIME rewriting without sufficient nested multipart fixture coverage.
- Authentik-specific OIDC configuration.
- SQLite-only persistence without migrations.
- Unsupported broad SPF/DMARC/DKIM deliverability claims.

These findings drove the work on the current branch. The current status is in the table above; do not infer that every integration or deployment risk is closed simply because the original code issue was remediated.

## 6. CI evidence

The latest successful push and pull-request CI runs both target commit `e49f8ce6322224cb7feda48764fe4810d2e0701f`:

- Push run #1822 (`43763421813`): success.
- Pull-request run #1823 (`43763427297`): success.
- 244 regression tests passed.
- Full-history Gitleaks scan and dependency audit passed.
- Python, JavaScript and Lua syntax checks and the Alpine CSP evaluator passed.
- PostgreSQL 17 and SQLite migrations passed.
- Docker build, Compose startup, healthcheck and Chromium/Playwright browser smoke passed.
- Scheduled backup guards, encrypted Docker backup, fresh restore and archive verification passed.
- Browser coverage includes local login, admin and normal-user authorization, signature/group/rule management, export/import preview, personal access, domain override policy, logout/session invalidation, and a real Quill typing/formatting assertion.

This evidence is automated regression and local-container evidence. It does not constitute live LDAP/OIDC provider validation, a cross-host disaster-recovery drill, real NFS/SMB validation, SMTP/DKIM integration, a dynamic penetration test or production certification.

## 7. Credential and history handling

The historical Rspamd engine credential must be rotated anywhere it may have been deployed, copied or stored. The value is intentionally not reproduced in this document. The old credential remains in immutable Git history because history rewriting was not authorized. Gitleaks allowlists only the known introducing commits; new findings remain subject to CI scanning.

## 8. Recommended remaining work

1. Complete live LDAP/OIDC sign-in and logout verification against the intended Active Directory and Authentik instances.
2. Keep CSP in report-only mode; investigate the two Quill `style-src-attr` reports before enforcement. The editor replacement remains a separate deferred task.
3. Execute the documented two-host restore drill and real NFS/SMB mount/unmount acceptance checks.
4. Test Rspamd in a disposable Mailcow environment: SMTP sender authorization, supported non-multipart behavior, idempotency and DKIM verification. Multipart messages remain unchanged by design.
5. Rotate the historical Rspamd engine credential anywhere it may have been deployed; the old value remains in Git history.
6. Validate rate-limit thresholds under realistic mail throughput and perform a focused dynamic security review.
7. Review proxy Host/forwarded-header behavior on the intended deployment path.

