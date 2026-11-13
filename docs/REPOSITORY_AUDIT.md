# Repository and security audit

**Repository:** `VeiglbergTech/OpenSigWeave`  
**Reviewed branch:** `architecture/refactor-foundation`  
**Review date:** 2026-11-11  
**Purpose:** Engineering review of the current standalone preview. This is not a security certification or penetration test.

## 1. Scope and baseline

The original repository was a compact FastAPI application with an Authentik-specific profile lookup, SQLite persistence and a Rspamd Lua adapter. The current branch has been refactored substantially. The old findings below are retained as historical context only; the status table describes the current branch.

## 2. Current implementation summary

- FastAPI application with optional OIDC login or a local scrypt-protected administrator account; group-based authorization is shared by both modes.
- Authentik API-backed employee attribute retrieval and Authentik-specific logout.
- PostgreSQL and Alembic for standalone persistence; SQLite compatibility remains.
- Hybrid signature storage: relational metadata plus versioned HTML files.
- Signature catalog, variants, weighted selection and persisted rules.
- Server-side HTML sanitization, autoescaped profile variables and sandboxed previews.
- Session-bound CSRF, secure session cookies, trusted-host checks, response headers and body-size limits.
- Shared database-backed rate limiting.
- Rspamd adapter with external configuration, HTTPS-only API URL, shared engine key, bounded timeout and response-size limits.
- Standalone Docker Compose stack with a non-root application container and persistent volumes.

## 3. Security findings and current status

| Area | Current status | Remaining risk/work |
|---|---|---|
| Historical engine credential | Removed from current Lua source; runtime configuration is external. | Treat the historical value as compromised and rotate wherever deployed. Git history was not rewritten. |
| Browser authorization | Admin and personal-user routes enforce group checks server-side. | Real-browser authorization walkthrough remains outstanding. |
| CSRF | Session-bound token required for state-changing browser requests. | Verify complete browser behavior and edge cases. |
| Session security | Strong startup secret validation; Secure, HttpOnly, SameSite cookie with limited lifetime. | Validate behind the intended reverse-proxy topology. |
| Authentication | OIDC validates HTTPS discovery, verified email, subject and groups; `OIDC_ENABLED=false` removes the callback route and uses a scrypt-protected local administrator. | Local-only mode has one environment-provisioned administrator and no external identity attributes. |
| Authentik API | HTTPS required, TLS verification enabled, bounded timeout, exact email match and active-user check. | Validate least-privilege service account permissions in a real Authentik instance. |
| HTML handling | Server-side allowlist sanitizer, re-sanitization before use, autoescaped identity data and sandboxed previews. | Real-browser testing and email-client rendering compatibility remain. |
| Host validation | TrustedHostMiddleware accepts the callback hostname and explicit aliases only. | Proxy Host/forwarded-header behavior must match deployment topology. |
| Request size | Global request-body limit and bounded signature HTML storage. | Validate operational limits with representative usage. |
| Rate limiting | Atomic database-backed counters shared across workers; login, legacy and managed OIDC callback paths, and signature-engine requests are covered; proxy IP headers trusted only from configured CIDRs. | Tune thresholds against real engine throughput. |
| File storage | Generated UUID names, path containment, symlink checks, atomic writes and SHA-256 integrity verification. | Exercise backup/restore and filesystem failure scenarios. |
| Docker isolation | Non-root UID, read-only root filesystem, dropped capabilities, no-new-privileges and loopback-only port. | Container image vulnerability scanning and runtime deployment review remain useful follow-up work. |
| Rspamd MIME rewrite | Multipart messages are left unchanged. | Only non-multipart rewrite path is currently supported; no full MIME or DKIM integration proof. |
| CSP | Restrictive policy is report-only; Alpine CSP build and SRI are used. | Real-browser CSP report review before enforcement. |

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

CI run #520 on commit `753deb6a8ac8ad2a893a396575c18b4e7eaa0a62` passed:

- Full-history Gitleaks scan.
- Python dependency audit.
- Python, browser JavaScript and Lua syntax checks.
- Alpine CSP evaluator smoke test.
- PostgreSQL and SQLite migrations.
- Docker image build and Compose startup.
- Container healthchecks and Alembic migration in the application container.
- Authenticated engine API smoke request against PostgreSQL.
- 102 regression tests.

This evidence covers automated checks and a container/API smoke test. It does not constitute a dynamic penetration test, browser acceptance test, SMTP integration test or production certification.

## 7. Credential and history handling

The historical Rspamd engine credential must be rotated anywhere it may have been deployed, copied or stored. The value is intentionally not reproduced in this document. The old credential remains in immutable Git history because history rewriting was not authorized. Gitleaks allowlists only the known introducing commits; new findings remain subject to CI scanning.

## 8. Recommended remaining work

1. Real-browser acceptance tests for login, roles, signature editor, rules and logout.
2. Review CSP reports and Quill runtime behavior before enforcing CSP.
3. Verify backup/restore of PostgreSQL and signature files as a consistent set.
4. Validate rate limits and resource usage under realistic load.
5. Test Rspamd in a disposable Mailcow environment: sender authorization, supported MIME cases, idempotency and DKIM.
6. Perform a focused dynamic security test before any production release.
