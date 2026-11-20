# Web application and Rspamd security review

**Development baseline:** `dev` (CI-validated candidate: `beta`)  
**Review date:** 2026-11-19  
**Status:** Code-level beta/release-candidate review plus automated security/browser evidence; not a penetration test or production certification.

## 1. Web application

### Authentication and authorization

- External OIDC authentication is available only through managed identity providers and their `/auth/{provider_id}` callbacks. The local emergency administrator uses a salted scrypt hash.
- The callback requires a subject, a syntactically valid email, `email_verified` as the JSON boolean `true`, and a well-formed groups array.
- The previous session is cleared before the authenticated user object is stored.
- The session stores only the application-required identity fields and selected profile attributes.
- Administrator routes check the `signature-admins` group server-side.
- Personal signature routes require `signature-users` or `signature-admins`.
- State-changing browser requests require a session-bound CSRF token. Logout uses POST and is covered by CSRF validation.
- Managed `/auth/{provider_id}` OIDC callbacks use the database-backed callback rate-limit policy.

**Provider distinctions:** Authentik remains the provider with dedicated account lookup through its API. Generic OIDC login and logout use standard discovery metadata; the verified callback claims are cached for profile use (currently up to 24 hours) because generic OIDC does not define a portable account-status or profile-management API. Consequently, group or account changes at a generic provider may not take effect until cache expiry. Do not describe generic OIDC as providing immediate account revocation. When no managed provider is assigned, the local administrator remains available independently; requests without an assigned profile provider return empty external identity attributes.

### Session and HTTP controls

- Startup rejects missing, short and known-placeholder session/engine secrets.
- Session cookie is Secure, HttpOnly, SameSite=Lax, uses the `__Host-` prefix and has a one-hour lifetime.
- TrustedHostMiddleware validates explicit hostnames from `APP_ALLOWED_HOSTS`; wildcards are rejected.
- Security response headers include content-type/frame restrictions, HSTS and other hardening headers.
- A global request-body limit rejects oversized/chunked requests; HTML fields and signature files have their own limits.
- Database-backed rate limiting is shared across workers. The application fails closed if the limiter database is unavailable.
- Forwarded client IP headers are ignored unless the immediate peer belongs to a CIDR in `TRUSTED_PROXY_CIDRS`.

Reverse proxies must preserve the canonical Host header. The supported deployment model assumes the reverse proxy and application may run on separate hosts: bind Compose port 8085 to the application host's private interface using `APP_BIND_ADDRESS`, then use the application host firewall to allow TCP/8085 only from the proxy's exact private source IP. Keep the port closed on public interfaces. Configure `TRUSTED_PROXY_CIDRS` with only the immediate proxy peer (`/32` or `/128`); this setting affects rate-limit client identification only and does not enforce network access. The application does not trust forwarding headers from an untrusted peer. If the proxy-to-application path is not already encrypted and authenticated, use TLS or a private authenticated tunnel.

### HTML, templates and browser rendering

- Signature HTML is sanitized server-side before storage and again before use.
- Dynamic identity values are rendered in a sandboxed Jinja environment with automatic HTML escaping.
- Signature previews are isolated in sandboxed iframes.
- CKEditor 5 49.1.0 is self-hosted. Client-side editor parsing is not treated as the security boundary; the server-side sanitizer remains authoritative.
- Alpine's CSP-compatible build is version-pinned and protected with SHA-384 SRI, but is still loaded from a CDN.
- The CSP is `Content-Security-Policy-Report-Only`, not enforced.

The jsdom smoke test covers the CSP evaluator and checks the actual template for unsupported Alpine expression patterns. Chromium/Playwright additionally captures `securitypolicyviolation` events during real browser administration/editor workflows. Keep CSP report-only until broader browser/deployment acceptance; do not add broad `unsafe-eval` or `unsafe-inline` directives merely to silence a future violation.

The current Chromium/Playwright CI flow signs in through the local administrator form, traverses the administration areas, exercises CKEditor-backed editing, signature/group/rule lifecycle operations, Scheduling create/update/delete, Branding logo/favicon upload and reset, selective export/import preview, normal-user/admin authorization boundaries, domain override policy, logout and session invalidation. It runs against the built Docker stack. This remains isolated CI evidence and does not replace live identity-provider, production MTA or real email-client acceptance.

The current report-only Chromium workflow records no CSP violation events across the exercised paths. Keep the policy report-only until broader supported-browser and deployment acceptance confirms that enforcement is safe; do not broaden the policy with `unsafe-inline` or `unsafe-eval` without a specific reviewed need.

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
- Supports concrete signature-name/checksum markers. The deprecated mode-selector header and old HTML selector markers are stripped for cleanup only and are never forwarded to the engine.
- Requires `task:get_user()` and uses the SMTP envelope sender from `task:get_from(1)`.

### Multipart behavior

The adapter now processes `multipart/alternative`, `multipart/related` and `multipart/mixed` through Rspamd's parsed MIME tree, decoding only `text/plain` and `text/html` leaves before placement and quoted-printable re-encoding. Binary attachments and inline CID/image parts are copied without decoding. Whole messages are skipped if they contain signed/encrypted or embedded message structures, malformed boundaries, undecodable text or exceed the 20 MiB rewrite limit. Raw boundary matching is not used.

This is covered by Lua mock-parsed MIME-tree tests plus Python MIME parsing of serialized nested messages. Real Rspamd/Mailcow handling, Content-Transfer-Encoding variants and final DKIM remain an external acceptance requirement. See [Mailcow integration](MAILCOW_RSPAMD_INTEGRATION.md).

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

## 4. Acceptance matrix

| Area | Test cases |
|---|---|
| Login | Valid OIDC login; invalid state; missing subject; unverified email; missing/malformed groups |
| Roles | Anonymous, normal user, administrator; direct API calls as well as UI visibility |
| CSRF | Missing, invalid and valid token for POST/PUT/DELETE; logout |
| Host/proxy | Canonical host, rejected unknown host, configured alias, proxy forwarding behavior |
| HTML | Script, event handler, unsafe URL, SVG payload, valid table layout, dynamic value escaping |
| Signature storage | Create version, read integrity, tamper detection, symlink rejection, backup/restore |
| Rules | Priority, disabled rules, AND conditions, group/attribute matching, direct signature/group/NONE targets |
| Engine API | Missing/wrong key, malformed email, concrete signature selector, oversized response, timeout |
| Rspamd MIME | Single-part plain text, single-part HTML, multipart/alternative, nested multipart, attachment, base64, quoted-printable |
| Mail behavior | New message, reply, forward, repeated processing, no matching signature |
| DKIM | Verify the final delivered message after injection |

The automated nested-multipart fixture now checks the rewritten plain/HTML alternatives and unchanged binary attachment. A disposable Mailcow SMTP/DKIM test is still required before enabling production flow.

## 5. CI evidence and limits

Application CI history is available from the branch-specific GitHub Actions views for [dev](https://github.com/VeiglbergTech/OpenSigWeave/actions?query=branch%3Adev) and [beta](https://github.com/VeiglbergTech/OpenSigWeave/actions?query=branch%3Abeta). The workflow scans complete Git history for secrets, audits Python dependencies, runs Bandit, checks browser JavaScript and CSP expressions, validates PostgreSQL/SQLite migrations, builds the Docker stack, runs Chromium administration workflows, verifies encrypted full backup/fresh restore and executes the regression suite.

Dedicated Postfix compatibility CI separately validates the real PyMilter/libmilter/Postfix mutation path with a mock engine.

This is automated regression and isolated-container evidence only. It is not a dynamic penetration test, live LDAP/OIDC integration test, cross-host disaster-recovery drill, real NFS/SMB storage test, live production SMTP integration, final DKIM verification or production security certification.
