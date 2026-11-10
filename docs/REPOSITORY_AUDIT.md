# Repository audit – baseline

**Repository:** `VeiglbergTech/OpenSigWeave`  
**Branch audited:** `main`  
**Audit branch:** `architecture/refactor-foundation`  
**Audit date:** 2026-11-10  
**Purpose:** Record the current implementation before refactoring. This document describes code observations, not a security certification.

## 1. Repository baseline

The repository is a small FastAPI application with a single Python entry point, a Rspamd Lua integration, Jinja templates, static assets, environment configuration and a pinned requirements file.

Observed top-level files/directories:

- `main.py`: application, SQLAlchemy models, database initialization, web routes, API routes, OIDC client setup and signature compilation.
- `rspamd/opensigweave.lua`: Rspamd prefilter which requests a signature and rewrites message content.
- `templates/`: server-rendered user interface.
- `static/`: frontend assets.
- `branding/`: optional branding examples.
- `.env.example`: runtime configuration template.
- `requirements.txt`: pinned Python dependencies.
- `README.md`: setup, feature and security claims.

The current database URL is hard-coded to SQLite. SQLAlchemy metadata is created at import time with `Base.metadata.create_all`; there is no migration system in the repository baseline. No automated test suite or CI workflow was present in the inspected tree.

## 2. Current application design

### Backend

`main.py` combines:
- SQLAlchemy model definitions for domains and user overrides.
- SQLite engine and session creation.
- Authlib OIDC registration with an Authentik-specific provider name and URL construction.
- Browser session handling.
- HTML page rendering and user/admin authorization.
- Domain and override CRUD endpoints.
- The engine endpoint consumed by Rspamd.
- Authentik Admin API lookup and HTML template interpolation.

The engine endpoint performs a live Authentik API request during message processing. This couples signature compilation to a specific identity provider and introduces an external network dependency into the mail-processing path.

### Current persistence model

- `domains`: domain name, active state, override/reply/whitespace/device-signature flags and an inline HTML template.
- `user_overrides`: unique email address, inline HTML and disabled flag.

There are no first-class signature variants, rule records, version records, per-sender selection state, audit events or explicit identity-provider abstractions.

### Rspamd integration

The Lua module:
- Registers `OPENSIGWEAVE_INJECT` as a Rspamd `prefilter`.
- Uses a fixed API URL and an API credential embedded in the Lua source.
- Requests `/api/signature/{sender_email}` using `X-Engine-Key`.
- Rewrites MIME body data and calls `task:set_message`.
- Contains custom handling for HTML/plain-text bodies, replies, whitespace and mobile-client sign-offs.

Because the module mutates the message during prefilter processing, the exact relationship between this action and the configured DKIM signing stage must be proven in a real Rspamd/Mailcow test environment. Do not claim DKIM safety until that test passes.

## 3. Findings requiring remediation

Severity is a prioritization for engineering work, not a formal CVSS score.

| ID | Severity | Observation | Required action |
|---|---|---|---|
| SEC-01 | Critical | A static engine credential is embedded in the tracked Lua source. | Treat as exposed; rotate/revoke the value if it was ever deployed. Move configuration to a protected runtime configuration file or environment-backed deployment mechanism. |
| SEC-02 | Critical | `GET/POST /domains/`, `PUT /domains/{domain_id}` and `DELETE /domains/{domain_id}` have no visible authentication/authorization dependency in the route definitions. | Require authenticated administrator authorization and test every method. |
| SEC-03 | High | Session middleware uses a fallback secret when `SECRET_KEY` is unset. | Fail startup if a strong secret is absent; set secure cookie options behind HTTPS. |
| SEC-04 | High | Authentik Admin API client uses `httpx.AsyncClient(verify=False)`. | Restore certificate validation; make trusted CA configuration explicit if needed. |
| SEC-05 | High | Browser state-changing endpoints rely on cookie sessions; no explicit CSRF protection is visible in the inspected routes. | Add CSRF protection and origin checks appropriate to the chosen browser/API design. |
| SEC-06 | High | HTML supplied by users/admins is stored and returned to the browser; the editor/preview path requires a deliberate trust and sanitization model. | Define allowed HTML, sanitize preview/output where appropriate, and test stored/reflected XSS cases. |
| SEC-07 | High | Rspamd mutates raw MIME content with custom parsing and reconstruction. | Add representative MIME fixtures and integration tests; verify DKIM ordering, multipart handling, transfer encodings and idempotency. |
| SEC-08 | Medium | OIDC configuration and logout URLs are Authentik-specific. | Use provider metadata/discovery configuration and standard OIDC claims; keep provider-specific extensions outside the core. |
| SEC-09 | Medium | Identity attributes are fetched synchronously from Authentik on every engine request. | Introduce an identity/attribute provider interface, caching policy and bounded failure behavior. |
| SEC-10 | Medium | Database schema is created at import time and has no migration history. | Introduce Alembic and explicit startup/deployment migration steps. |
| SEC-11 | Medium | No automated tests or CI configuration were found in the inspected tree. | Establish unit, API, database and Rspamd integration test layers. |
| SEC-12 | Medium | README makes broad SPF/DMARC/DKIM deliverability claims without test evidence. | Replace absolute claims with accurately scoped behavior and documented validation results. |

**Credential handling:** This audit intentionally does not reproduce the embedded credential. Rotate it anywhere it may have been copied, deployed, logged or cached. Removing it from a future commit does not erase it from Git history.

## 4. Important implementation risks

1. **DKIM ordering:** The Lua symbol is a prefilter, but that alone is not sufficient evidence of the final configured processing order. Inspect the effective Rspamd configuration and verify the final raw message and DKIM result in an isolated test setup.
2. **MIME correctness:** The module manually locates MIME headers/boundaries and decodes/re-encodes content. Nested multipart structures, folded headers, unusual boundary formatting, attachments, charsets and malformed messages need fixtures before further feature work.
3. **Idempotency:** Ensure a message cannot receive the signature twice if the symbol is re-run or a message is reinjected.
4. **Sender trust:** Confirm the envelope sender, authenticated SMTP identity and message From header cannot be confused to select another user's signature.
5. **Failure policy:** Define whether a backend timeout or invalid response means fail-open (send without signature) or fail-closed (defer/reject). This must be configurable and documented.
6. **HTML safety:** Signature HTML must support legitimate email markup without allowing the management interface to become an XSS vector.
7. **Data freshness:** Define which attributes are authoritative, how stale identity data is handled and whether mail processing may depend on an online identity service.

## 5. Target architecture decisions

The following decisions were supplied by the project owner:

- PostgreSQL as the database.
- OIDC as the authentication protocol.
- Hybrid signature storage: HTML files plus relational metadata.
- Rspamd as the first mail-server integration.

Engineering implications:

- PostgreSQL is the supported application database; use SQLAlchemy 2 and Alembic.
- OIDC login should use discovery metadata and standard claims. Do not make Authentik a hard dependency of the signature core.
- Identity attributes should be provided through an interface. OIDC claims can be the initial source; LDAP/AD enrichment can be a later provider.
- HTML files should be stored below a configured root directory. Database records should hold stable identifiers, metadata, version/hash information and activation state. Never trust arbitrary filesystem paths from API input.
- The signature engine and rule engine must not import Rspamd-specific code.
- The Rspamd adapter should have a small, versioned internal API contract and explicit timeout/error behavior.

## 6. Recommended implementation sequence

### Milestone 1 – Safe foundation
1. Rotate the exposed engine credential in any deployed environment.
2. Document the current request flow and Rspamd/DKIM verification procedure.
3. Add baseline tests for current API behavior and MIME samples before refactoring.
4. Add configuration validation and remove insecure defaults.
5. Protect all administrative endpoints and add CSRF protection.

### Milestone 2 – Modular backend
1. Extract configuration and database session management.
2. Introduce Alembic and PostgreSQL models.
3. Extract API routers, identity provider interfaces and authorization policy.
4. Extract signature rendering, rule evaluation and variant selection into independent core services.

### Milestone 3 – Signature capabilities
1. Add file-backed signature versions and integrity hashes.
2. Add variants, explicit modes and prioritized rules.
3. Add transactional per-sender no-immediate-repeat selection.
4. Add audit logging and rollback support.

### Milestone 4 – Rspamd and Mailcow
1. Define a stable internal engine API.
2. Make Lua configuration external and secret-safe.
3. Build MIME regression fixtures and integration tests.
4. Verify the final message is modified before DKIM signing in a disposable Mailcow/Rspamd environment.

## 7. Scope and non-goals for this branch

This branch is a staged refactor, not a rewrite. Preserve existing UI and behavior where practical. Do not deploy changes to a production Mailcow installation as part of repository development. Do not claim a security issue is fixed until a corresponding test or review demonstrates the fix.


## 8. Source locations (baseline line numbers)

Line numbers refer to the inspected baseline files on `main`; they may move during refactoring.

| Finding | Source location |
|---|---|
| SQLite URL hard-coded | `main.py:21` |
| Schema created at import time | `main.py:44` |
| Fallback session secret | `main.py:69` |
| Domain CRUD without route-level auth dependency | `main.py:184-214` |
| Admin override routes do perform an admin check | `main.py:247-271` |
| Authentik TLS verification disabled | `main.py:328` |
| Static engine credential in Lua | `rspamd/opensigweave.lua:9` |
| Rspamd HTTP request has no explicit timeout in request options | `rspamd/opensigweave.lua:364-370` |
| Message rewrite via `task:set_message` | `rspamd/opensigweave.lua:339` and `main.py`-independent MIME paths |
| User-controlled profile values interpolated into JavaScript | `templates/index.html:40` |
| Preview uses Alpine `x-html` | `templates/index.html:381` |
| Domain API called directly by admin UI | `templates/index.html:460`, `510-524` |
| HTML import removes script tags using regex | `templates/index.html:437-442` |
| Third-party editor/framework scripts loaded from CDNs | `templates/index.html:11-12` |

The domain routes are not protected merely because the template only displays the admin tab to administrators. The route handlers themselves must enforce the policy. In contrast, the inspected admin override routes explicitly call `is_admin_user`; this distinction should be retained in future reviews.


## 9. Remediation status on `architecture/refactor-foundation`

The findings above describe the original baseline. The following changes have since been committed on this branch only:

| Finding | Branch status | Remaining work |
|---|---|---|
| SEC-01 embedded Lua credential | Removed from current Lua source; configuration now comes from the Rspamd `opensigweave` section and requires HTTPS. | Rotate any previously deployed/copied key. The old value remains in Git history until history is rewritten; do not treat source removal as rotation. |
| SEC-02 unauthenticated domain CRUD | All four domain CRUD routes now require the configured admin group. | Add runtime API tests using authenticated/anonymous test clients. |
| SEC-03 weak session fallback | Startup rejects missing, short and known-placeholder secrets; Secure and SameSite=Lax cookies configured. | Validate deployment behavior behind each supported reverse-proxy topology. |
| SEC-04 disabled TLS verification | Authentik API client now verifies TLS and has an explicit timeout. | Add mocked HTTP tests for invalid certificates and timeouts. |
| SEC-05 CSRF | Session-bound CSRF token enforced on POST/PUT/PATCH/DELETE for authenticated browser sessions; UI fetch wrapper sends the token and logout now uses POST. | Add runtime tests and review logout semantics (currently GET). |
| SEC-06 HTML/XSS | Signature previews use sandboxed iframes; JavaScript profile data uses JSON serialization; Quill content is loaded through its clipboard parser. | Define and test a full server-side policy for stored signature HTML and imported content. |
| SEC-07 MIME/DKIM | No functional MIME rewrite changes made yet. | Build MIME regression fixtures and verify actual DKIM ordering in isolated Mailcow. |

These are code changes and documented controls, not a production deployment or a completed security certification. The latest GitHub Actions run passed: Python compilation, Lua syntax validation, PostgreSQL and SQLite migrations, and all 40 regression tests.
