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
- Extracted employee attribute retrieval behind an `AttributeProvider` protocol and an Authentik implementation. The signature endpoint no longer performs Authentik HTTP requests directly; a later OIDC-claims or LDAP provider can replace it. Logout remains Authentik-specific.
- Added standard-library tests for domain-route authorization, CSRF validation wiring, sandboxed preview rendering, safe editor HTML parsing, sandboxed autoescaped signature templates, identity provider behavior, mode/rule/variant selection, session-secret validation, TLS verification and database configuration. A legacy SQLite data-preservation test is included.
- Added a GitHub Actions workflow to compile Python sources, lint Lua syntax, apply migrations against PostgreSQL and SQLite, and run regression tests. The latest complete run passed all 40 tests, Python compilation, Lua syntax validation, and PostgreSQL/SQLite migrations. Workflow triggers are pull requests and manual dispatch.

### Documentation

- Added repository audit, target architecture, and web/Rspamd integration review documents.
- Added concrete baseline source references to the audit.

## Not yet complete


- Full HTML sanitization policy for editor/import/save paths. Signature previews are now rendered in sandboxed iframes and profile values are serialized with Jinja's JSON filter; visual editor content now passes through Quill's clipboard parser instead of assigning untrusted HTML directly to the editor DOM. A complete server-side sanitization policy for stored/output HTML is still pending.
- OIDC state/nonce and authorization hardening review.
- Rotation of the previously embedded engine credential in any deployed environment and removal from Git history. Removing it from the current Lua file does not invalidate copies in earlier commits or deployed systems.
- Rspamd idempotency and MIME correctness work; HTTP timeout/TLS/URL encoding/payload size handling and credential externalization are now in place.
- Verification of Rspamd/DKIM processing order in an isolated Mailcow test environment.
- The transactional selection repository exists and is unit-tested on SQLite. It is not yet wired into the Rspamd message API, and concurrent PostgreSQL integration testing remains. The core mode resolver and prioritized rule evaluator are implemented but not yet connected to the message-processing API.
- SMTP-header and Outlook marker parsing/removal for explicit signature modes remains pending.
- Runtime API tests and full integration tests remain pending.
- Further model/service extraction, integration of file storage with the signature management API, transactional selection-state updates, and end-to-end PostgreSQL application testing. PostgreSQL and SQLite migrations now include the signature/version/variant/selection/rule/condition tables.

## Important operational note

The new startup validation means an existing deployment with no valid `SECRET_KEY` will no longer start from this branch. Configure a strong secret before testing the branch. Session cookies are Secure, so access the web UI over HTTPS. This is an intentional fail-safe, not a production migration performed by this project work.
