# Implementation progress

This file tracks changes made on `architecture/refactor-foundation`. It is not a release note and does not imply production deployment.

## Completed on this branch

### Security baseline

- Domain list/create/update/delete routes now check the authenticated administrator group in the route handler. UI visibility is not treated as authorization.
- Application startup now refuses empty, short or known-placeholder `SECRET_KEY` and `ENGINE_API_KEY` values.
- Added session-bound CSRF validation for state-changing browser requests. The UI sends the per-session token automatically; requests without a valid token are rejected. Logout was changed from GET to POST.
- Isolated signature preview rendering in sandboxed iframes, changed profile-data interpolation to JSON-safe serialization, and moved signature rendering to a sandboxed Jinja environment with automatic HTML escaping of identity attributes.
- Changed editor HTML loading to use Quill's clipboard parser instead of direct `innerHTML` assignment. Clarified that the import helper is not a complete security sanitizer.
- Session cookies are configured with `SameSite=Lax` and `Secure`.
- Authentik API requests use TLS certificate verification and an explicit five-second timeout.
- The example environment file explains that session and engine secrets must be generated separately and kept out of source control.
- The README's systemd example now uses a dedicated unprivileged service account and an explicit environment file.

### Regression checks

- Extracted the SQLAlchemy engine, session factory and request-scoped session lifecycle into `database.py`; the URL is now configurable through `DATABASE_URL`, and the Psycopg 3 driver is included for PostgreSQL. SQLite remains the compatibility default until the migration milestone.
- Login now registers a provider-neutral OIDC client using `OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET`. Legacy Authentik values remain as fallback for compatibility.
- Extracted employee attribute retrieval behind an `AttributeProvider` protocol and an Authentik implementation. The provider now exposes profile fields, groups and custom attributes to the rule engine. The signature endpoint remains provider-neutral; a later OIDC-claims or LDAP provider can replace the Authentik implementation. Logout remains Authentik-specific.
- Added standard-library tests for domain-route authorization, CSRF validation wiring, sandboxed preview rendering, safe editor HTML parsing, sandboxed autoescaped signature templates, identity provider behavior, mode/rule/variant selection, session-secret validation, TLS verification and database configuration. A legacy SQLite data-preservation test is included.
- Added a GitHub Actions workflow to compile Python sources, lint Lua syntax, apply migrations against PostgreSQL and SQLite, and run regression tests. The latest complete run on commit `e8410b38eb150a975257be64fa997e86fb1a68fa` passed all 67 tests, Python compilation, Lua syntax validation, and PostgreSQL/SQLite migrations. Runtime tests exercise signature creation, metadata changes, version creation/listing, variant management, admin authorization, domain override policy, protection of administrator-disabled overrides, and case-insensitive email matching. Workflow triggers are pull requests and manual dispatch.

### Documentation

- Added repository audit, target architecture, and web/Rspamd integration review documents.
- Added concrete baseline source references to the audit.

## Not yet complete


- Server-side HTML sanitization is now implemented at the catalog storage boundary for both initial signature creation and every subsequent version. The `nh3` allowlist retains common email layout markup and a constrained inline-style set while stripping scripts, event handlers, active embedded content, unsafe URL schemes and relative URLs. Regression tests cover common XSS payloads. The editor/import pipeline still needs a dedicated browser-level review; client-side Quill parsing is not treated as the security boundary.
- OIDC state/nonce and authorization hardening review.
- Rotation of the previously embedded engine credential in any deployed environment and removal from Git history. Removing it from the current Lua file does not invalidate copies in earlier commits or deployed systems.
- Rspamd MIME handling is deliberately fail-closed for top-level multipart messages: the integration logs a warning and leaves the message unchanged rather than applying unsafe raw-boundary regex rewriting. Flat non-multipart text/plain remains the only supported mutation path at present. A parser-backed multipart rewrite is still required before normal HTML/attachment-rich mail flows can be enabled. Rspamd 4.x reports also indicate `lua_mime.add_text_footer()` may return no rewrite for multipart structures; do not assume that helper solves this without version-specific tests. Nested multipart, related/alternative, attachments, base64, quoted-printable and inline CID test fixtures are pending. HTTP timeout/TLS/URL encoding/payload size handling and credential externalization are in place.
- Verification of Rspamd/DKIM processing order in an isolated Mailcow test environment.
- The transactional selection repository is connected to the Rspamd message API. Rule evaluation now loads persisted rules, evaluates sender/domain plus Authentik group/custom-attribute conditions, and selects from the configured mode pool. Rules can target a specific signature; variant selection supports that target and still avoids immediate repetition per sender and mode. Explicit engine mode headers take precedence over user overrides and rules; otherwise a non-disabled user override takes precedence over rules. Concurrent PostgreSQL integration testing remains.
- Added a database-backed rule repository that maps persisted rule and condition rows into the provider-neutral rule engine. Added tests for priority, disabled rules, AND-combined conditions and explicit signature targets.
- Added administrator-protected CRUD API endpoints and a web management interface for listing, creating, editing and deleting signature rules. Rule conditions are validated against supported condition types and replaced transactionally on update. Target signatures are validated against the rule action mode.
- Personal override save/delete endpoints now enforce domain override policy and cannot be used by users to remove an administrator-disabled override. The engine also ignores existing personal overrides when the domain has disabled them. Email lookups are normalized case-insensitively across user, administrator and engine routes.
- The signature API accepts an authenticated-engine `X-OpenSigWeave-Mode` override (`AUTO`, `PROFESSIONAL`, `INTERNAL` or `NONE`). Rspamd accepts a single validated mode header on authenticated submissions, forwards it to the API, and removes it from the message; duplicate/unsupported values are ignored. When no valid header exists, Rspamd also checks decoded HTML text parts for Outlook comment/data-attribute markers, ignores markers found only after recognized quoted-reply boundaries, fails closed on ambiguous replies, and strips markers before injection. Real Outlook-version preservation tests remain pending.
- The latest completed CI run on commit `6e63a99c9254905ad54288c50efa84c149a69e60` passed Python compilation, Lua syntax validation, PostgreSQL and SQLite migrations, and all 70 regression tests. The new PostgreSQL concurrency test and PostgreSQL-backed regression execution were added afterward and are awaiting CI. Runtime API and full mail-flow integration tests remain pending.
- The signature catalog and file-storage service are connected to signature create/version/variant API endpoints. Administrator APIs support signature creation, version history, metadata updates (including deactivation and domain scope), variant listing, and variant weight/enabled-state changes. The admin web UI now provides signature creation/editing, new-version uploads, activation controls, and variant management. Service-level regression tests cover metadata updates and inactive signature creation. Variant deletion and end-to-end PostgreSQL application testing remain.

## Important operational note

The new startup validation means an existing deployment with no valid `SECRET_KEY` will no longer start from this branch. Configure a strong secret before testing the branch. Session cookies are Secure, so access the web UI over HTTPS. This is an intentional fail-safe, not a production migration performed by this project work.

## Security review — 2026-11-11

A branch-wide source review identified and addressed the following findings on `architecture/refactor-foundation`:

- Personal signature API handlers now require membership in `signature-users` or `signature-admins`; authentication alone is insufficient.
- User override lookups now use literal case-insensitive equality (`lower(column) = normalized_value`) rather than SQL `LIKE` matching. This prevents local-part characters such as `_` and `%` from acting as wildcards while preserving case-insensitive matching of legacy records.
- Personal overrides, administrator overrides and domain templates are sanitized before storage. Legacy stored HTML is sanitized again before it is returned to the personal editor, administrator editor, web preview and Rspamd engine.
- Domain names are normalized and validated before creation or administrative domain-override lookup. Domain suffix matching uses an escaped SQLAlchemy `endswith` expression.
- Authentik attribute lookup now requires exactly one exact case-insensitive email match and an explicitly active account; it no longer trusts the first partial search result.
- OIDC login now requires a subject, a verified email and a well-formed groups claim, clears any previous session before establishing the authenticated session, and stores only the application-required user claims.
- Session lifetime is limited to one hour. The cookie uses the `__Host-` prefix, Secure, HttpOnly, `Path=/` and SameSite=Lax. Standard response hardening headers and `Cache-Control: no-store, private` are applied to authenticated responses.
- A global 1 MiB request-body limit rejects oversized and chunked requests; legacy HTML inputs are separately capped at 64 KiB. Signature storage also enforces a 64 KiB file limit and rejects symlinks.
- Quill was upgraded from 1.3.6 to 1.3.7 to address the documented reverse-tabnabbing issue. Alpine.js is pinned to the exact 3.17.5 version rather than a floating 3.x alias. External links opened in a new tab use `noopener noreferrer`.

### Security review limitations and follow-up

- The review so far is static code review plus targeted unit/runtime regression tests, not a full dynamic penetration test.
- Content Security Policy remains report-only. Inline scripts, event handlers and style blocks have been externalized; the portal now uses Alpine's CSP-compatible build 3.17.5 and named components for the complex actions. The report-only policy is not yet an enforced security boundary.
- Subresource Integrity hashes and local vendoring for Alpine remain outstanding. Quill is self-hosted; Alpine's CSP build is pinned to 3.17.5 on jsDelivr and remains a third-party runtime dependency until vendored or protected with a verified SRI hash.
- OIDC provider configuration must expose the standard `email_verified` claim. This is intentionally fail-closed; deployments that do not provide it must correct their provider mapping rather than silently accepting unverified identities.
- GitHub branch protection and a complete Git-history secret scan remain outstanding. GitHub Actions are now pinned to immutable commit SHAs and checkout credential persistence is disabled.
- Existing database rows are not rewritten by this change. Read paths sanitize legacy HTML, and all new writes sanitize before persistence.
- No production Mailcow deployment or configuration was modified.

- OIDC login and logout now use the explicit HTTPS `OIDC_REDIRECT_URI`; the callback is no longer constructed from the incoming Host header. Configure the exact callback URI at the identity provider and in `.env`.
- Python dependencies were upgraded after `pip-audit` found 40 known vulnerabilities in six packages. The affected packages were updated, the deprecated HTTPX integration was migrated to HTTPX2, and the latest CI dependency audit now reports **no known vulnerabilities**. FastAPI 0.162.4 uses Starlette 1.10.0; Python 3.11+ is now the documented minimum.
- Latest CI on this branch passes dependency auditing, Python compilation, Rspamd Lua syntax, PostgreSQL and SQLite migrations, and all 82 regression tests.
- Signature storage now enforces mode `0700` on its dedicated root, opens assets with `O_NOFOLLOW`, verifies regular-file type, and refuses symlink deletion. Regression tests cover symlinked assets and directory permissions.
- Authenticated portal/API responses are marked `Cache-Control: no-store, private`. The editor's external dependencies are version-pinned; CDN SRI/local vendoring remains a follow-up.

- Documented the SMTP sender-authorization boundary for Mailcow: `task:get_user()` proves SMTP authentication but does not authorize the envelope sender. Mailcow/Postfix sender verification must remain enabled, and aliases/delegated send-as identities must be granted explicitly. Added a disposable-environment test matrix for legitimate aliases, delegated send-as, unrelated senders and unauthenticated submissions. This is a documented prerequisite only; it has not been validated against a live Mailcow instance and no production configuration was changed.
- Inspected the original Rspamd engine credential in the earliest Git commit without reproducing its value. It is not an obvious placeholder and should continue to be treated as compromised. Credential rotation remains required wherever it may have been deployed; Git history has not been rewritten.

- Added an explicit trusted-host allowlist. The canonical hostname is taken from the fixed HTTPS `OIDC_REDIRECT_URI`; additional reverse-proxy host aliases must be explicitly listed in `APP_ALLOWED_HOSTS`. Wildcard hosts are rejected at startup. Runtime tests verify that the canonical host is accepted and an attacker-controlled Host header is rejected. This closes a separate Host-header trust boundary; OIDC callback generation already uses the fixed redirect URI.


- Hardened the Authentik attribute provider against bearer-token disclosure: a configured `AUTHENTIK_URL` must use HTTPS and cannot contain embedded credentials, query parameters or fragments. Added regression tests for plaintext HTTP and URL-embedded credentials. This prevents a mistyped or malicious base URL from sending the service-account token over an unencrypted connection.

- Trusted-host middleware regression tests exposed two test-harness edge cases: early Host rejection can occur before SessionMiddleware has populated the request scope, and TestClient follows the valid-host login redirect unless explicitly told not to. The security-header middleware now safely handles requests rejected before session creation, and the canonical-host test disables redirect following. These fixes are included in the current branch; CI for the latest commit is pending.
- Authentik's service-token provider now refuses plaintext HTTP and URL-embedded credentials. The CI run before this final fix confirmed dependency audit, Python compilation, Lua syntax and both database migration jobs; the regression suite caught the two Host-test issues above, which have been corrected.


- Tightened OIDC email verification to require the JSON boolean `true` exactly. Previously, a malformed truthy value such as the string `"false"` could pass a generic truthiness check. Added tests for boolean false, string false, integer and missing claims, plus the valid boolean-true case.
- Added HSTS with a one-year max-age (without `includeSubDomains` or preload) and disabled legacy cross-domain policy files through response headers. The canonical-host runtime test checks both headers. HSTS is effective in browsers on HTTPS responses; TLS termination must remain at the trusted reverse proxy.


- Added shared database-backed fixed-window rate limiting for OIDC login, the OIDC callback and the Rspamd signature endpoint. PostgreSQL upserts make counters atomic across workers and replicas; SQLite support is retained for compatibility tests. The limiter fails closed when its database is unavailable, returns HTTP 429 with Retry-After when limits are exceeded, and stores only hashed bucket identifiers.
- Added Alembic revision `0004_rate_limit_buckets`, configurable per-endpoint thresholds and optional trusted reverse-proxy CIDRs. Forwarded client addresses are ignored unless the immediate TCP peer belongs to an explicitly configured trusted network. Unit tests cover counter increments, window resets, bucket isolation, hashed storage and forwarded-header trust boundaries.
- Default rate limits are 10 login requests/5 minutes, 20 callback requests/5 minutes and 1,800 engine requests/minute. The engine threshold must be reviewed against measured production mail throughput before rollout. Apply migration 0004 before deploying this code.


- Rate-limit bucket identifiers are pseudonymized with a keyed HMAC. The HMAC key is derived with domain separation from the mandatory `SECRET_KEY`; a database-only disclosure therefore does not make low-entropy client IP addresses directly enumerable. Regression tests verify that stored bucket keys are not plain SHA-256 hashes.

### Rate-limit key configuration correction — 2026-11-11

A review of the shared rate limiter found that its HMAC pseudonymization key was read from
`SESSION_SECRET`, while the application and `.env.example` configure the session secret as
`SECRET_KEY`. This mismatch caused protected requests to fail closed with HTTP 503 unless
an undocumented second variable was manually supplied. The limiter now derives its
domain-separated HMAC key from the configured `SECRET_KEY`, and regression tests cover
both successful derivation and placeholder rejection. CI validation is pending.
