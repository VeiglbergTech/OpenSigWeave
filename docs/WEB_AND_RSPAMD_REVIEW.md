# Web application and Rspamd security review

**Branch:** `architecture/refactor-foundation`  
**Review date:** 2026-11-13  
**Status:** Code-level review of the standalone preview; not a penetration test.

## 1. Web application

### Authentication and authorization

- OIDC login (when enabled) uses discovery metadata and an explicit HTTPS callback URI. `OIDC_ENABLED=false` disables OIDC setup/callback registration and uses a local administrator login verified against a salted scrypt hash.
- The callback requires a subject, a syntactically valid email, `email_verified` as the JSON boolean `true`, and a well-formed groups array.
- The previous session is cleared before the authenticated user object is stored.
- The session stores only the application-required identity fields and selected profile attributes.
- Administrator routes check the `signature-admins` group server-side.
- Personal signature routes require `signature-users` or `signature-admins`.
- State-changing browser requests require a session-bound CSRF token. Logout uses POST and is covered by CSRF validation.
- Legacy `/auth` and managed `/auth/{provider_id}` OIDC callbacks share the database-backed callback rate-limit policy.

**Known provider limitation:** In OIDC mode, the attribute provider and logout URL still use Authentik-specific APIs/paths. Authentik is the currently supported complete external provider configuration. A generic OIDC provider cannot yet be assumed to provide all application behavior. Local-only mode bypasses the external provider entirely and returns empty identity attributes.

### Session and HTTP controls

- Startup rejects missing, short and known-placeholder session/engine secrets.
- Session cookie is Secure, HttpOnly, SameSite=Lax, uses the `__Host-` prefix and has a one-hour lifetime.
- TrustedHostMiddleware derives the canonical hostname from `OIDC_REDIRECT_URI` in OIDC mode. Local-only mode requires explicit `APP_ALLOWED_HOSTS`. Wildcards are rejected.
- Security response headers include content-type/frame restrictions, HSTS and other hardening headers.
- A global request-body limit rejects oversized/chunked requests; HTML fields and signature files have their own limits.
- Database-backed rate limiting is shared across workers. The application fails closed if the limiter database is unavailable.
- Forwarded client IP headers are ignored unless the immediate peer belongs to a CIDR in `TRUSTED_PROXY_CIDRS`.

Reverse proxies must preserve the canonical Host header. Only configure trusted proxy CIDRs for proxies under administrative control. Do not trust client-supplied forwarding headers.

### HTML, templates and browser rendering

- Signature HTML is sanitized server-side before storage and again before use.
- Dynamic identity values are rendered in a sandboxed Jinja environment with automatic HTML escaping.
- Signature previews are isolated in sandboxed iframes.
- Editor HTML is parsed through Quill's clipboard parser; client-side parsing is not treated as the security boundary.
- Quill is self-hosted. Alpine's CSP-compatible build is version-pinned and protected with SHA-384 SRI, but is still loaded from a CDN.
- The CSP is `Content-Security-Policy-Report-Only`, not enforced.

The jsdom smoke test covers the CSP evaluator and checks the actual template for unsupported Alpine expression patterns. It does not replace a real-browser walkthrough. Before enforcing CSP, inspect actual browser reports, preview behavior and Quill's runtime style behavior. Do not add broad `unsafe-eval` or `unsafe-inline` directives merely to silence violations.

## 2. Authentik attribute provider

The attribute provider:

- Requires an HTTPS Authentik base URL with no embedded credentials, query or fragment.
- Verifies TLS certificates and uses a bounded HTTP timeout.
- Searches for the requested email and requires exactly one case-insensitive exact email match.
- Requires the Authentik account to be active.
- Returns normalized name, phone/title custom attributes, groups and selected user claims.
- Returns empty identity attributes if the lookup fails or does not produce one active exact match.

Use a dedicated Authentik service account/API token with the minimum read permissions needed for user lookup. Do not put this token in the frontend, source control or Rspamd configuration.

## 3. Rspamd adapter

The Lua adapter:

- Loads `api_url`, `engine_api_key`, `timeout` and `max_response_size` from the Rspamd `opensigweave` configuration section.
- Requires an HTTPS API URL and an engine key of at least 32 characters.
- Bounds the HTTP timeout and maximum response size.
- URL-encodes the sender email in the API path.
- Sends the engine key in `X-Engine-Key`.
- Supports one validated `X-OpenSigWeave-Mode` header on authenticated submissions, removes the control header and otherwise checks the supported Outlook HTML markers.
- Requires `task:get_user()` and uses the SMTP envelope sender from `task:get_from(1)`.

### Multipart behavior

The adapter detects the top-level Content-Type. If it is `multipart/*`, it logs a warning and returns without changing the message.

This includes common `multipart/alternative`, `multipart/related` and `multipart/mixed` messages. The current rewrite path is limited to non-multipart messages. This is deliberate fail-safe behavior; do not attempt to enable multipart rewriting through regular-expression boundary reconstruction.

### Sender authorization

`task:get_user()` only indicates that the message was submitted through an authenticated SMTP session. It does not prove that the authenticated mailbox may use the envelope sender.

Mailcow/Postfix sender verification must remain enabled. Aliases and delegated send-as identities must be granted explicitly. Do not create broad send-as permissions or broad Postfix restriction exceptions to make signature injection work.

### DKIM and processing order

The fact that the Lua module registers a prefilter does not prove that signature mutation occurs before DKIM signing in every Mailcow/Rspamd version and configuration.

Before enabling the adapter in a mail flow, use a disposable Mailcow environment and verify:
1. The effective Rspamd processing order.
2. Signature insertion occurs before DKIM signing.
3. The final delivered message passes DKIM verification.
4. No later stage mutates the signed message.
5. Repeated processing does not inject duplicate signatures.

No production Mailcow configuration has been changed and no end-to-end SMTP/DKIM test has been completed.

## 4. Suggested preview acceptance matrix

| Area | Test cases |
|---|---|
| Login | Valid OIDC login; invalid state; missing subject; unverified email; missing/malformed groups |
| Roles | Anonymous, normal user, administrator; direct API calls as well as UI visibility |
| CSRF | Missing, invalid and valid token for POST/PUT/DELETE; logout |
| Host/proxy | Canonical host, rejected unknown host, configured alias, proxy forwarding behavior |
| HTML | Script, event handler, unsafe URL, SVG payload, valid table layout, dynamic value escaping |
| Signature storage | Create version, read integrity, tamper detection, symlink rejection, backup/restore |
| Rules | Priority, disabled rules, AND conditions, group/attribute matching, targeted signature mode |
| Engine API | Missing/wrong key, malformed email, valid mode, duplicate mode header, oversized response, timeout |
| Rspamd MIME | Single-part plain text, single-part HTML, multipart/alternative, nested multipart, attachment, base64, quoted-printable |
| Mail behavior | New message, reply, forward, repeated processing, no matching signature |
| DKIM | Verify the final delivered message after injection |

The multipart cases should currently confirm that the message remains unchanged. They must not be interpreted as supported injection cases.

## 5. CI evidence and limits

The latest push and pull-request CI runs `43442572622` and `43442582940` on commit `30c3b0cb8b707ffc799fddd83bac829da7de3e35` both passed. They include full-history Gitleaks, dependency audit, Python/JavaScript/Lua checks, Alpine CSP smoke test, PostgreSQL and SQLite migrations, Docker build and stack smoke test, scheduled remote-filesystem guard tests, encrypted full-backup/fresh-restore integration, verified retention, selective administrator export checks and 202 regression tests.

This is automated regression and deployment-smoke evidence only. It is not a dynamic penetration test, real-browser acceptance test, SMTP integration test or production security certification.
