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
- Added a GitHub Actions workflow to compile Python sources, lint Lua syntax, apply migrations against PostgreSQL and SQLite, and run regression tests. The latest complete run on commit `eaffd07de8f78fcb577d9e8229403a25babe9842` passed all 60 tests, Python compilation, Lua syntax validation, and PostgreSQL/SQLite migrations. Workflow triggers are pull requests and manual dispatch.

### Documentation

- Added repository audit, target architecture, and web/Rspamd integration review documents.
- Added concrete baseline source references to the audit.

## Not yet complete


- Full HTML sanitization policy for editor/import/save paths. Signature previews are now rendered in sandboxed iframes and profile values are serialized with Jinja's JSON filter; visual editor content now passes through Quill's clipboard parser instead of assigning untrusted HTML directly to the editor DOM. A complete server-side sanitization policy for stored/output HTML is still pending.
- OIDC state/nonce and authorization hardening review.
- Rotation of the previously embedded engine credential in any deployed environment and removal from Git history. Removing it from the current Lua file does not invalidate copies in earlier commits or deployed systems.
- Rspamd idempotency and MIME correctness work; HTTP timeout/TLS/URL encoding/payload size handling and credential externalization are now in place.
- Verification of Rspamd/DKIM processing order in an isolated Mailcow test environment.
- The transactional selection repository is connected to the Rspamd message API. Rule evaluation now loads persisted rules, evaluates sender/domain plus Authentik group/custom-attribute conditions, and selects from the configured mode pool. Rules can target a specific signature; variant selection supports that target and still avoids immediate repetition per sender and mode. Explicit engine mode headers take precedence over user overrides and rules; otherwise a non-disabled user override takes precedence over rules. Concurrent PostgreSQL integration testing remains.
- Added a database-backed rule repository that maps persisted rule and condition rows into the provider-neutral rule engine. Added tests for priority, disabled rules, AND-combined conditions and explicit signature targets.
- Added administrator-protected CRUD API endpoints and a web management interface for listing, creating, editing and deleting signature rules. Rule conditions are validated against supported condition types and replaced transactionally on update. Target signatures are validated against the rule action mode.
- The signature API accepts an authenticated-engine `X-OpenSigWeave-Mode` override (`AUTO`, `PROFESSIONAL`, `INTERNAL` or `NONE`). Rspamd accepts a single validated mode header on authenticated submissions, forwards it to the API, and removes it from the message; duplicate/unsupported values are ignored. When no valid header exists, Rspamd also checks decoded HTML text parts for Outlook comment/data-attribute markers, ignores markers found only after recognized quoted-reply boundaries, fails closed on ambiguous replies, and strips markers before injection. Real Outlook-version preservation tests remain pending.
- Runtime API tests and full integration tests remain pending. The CI run for commit 8146cb515ca4d49afa6d41086915335397522994 completed successfully: Python compilation, Lua syntax validation, PostgreSQL and SQLite migrations, and all regression tests passed. Signature-catalog API changes made after that commit require another CI run.
- The signature catalog and file-storage service are connected to signature create/version/variant API endpoints. Administrator APIs support signature creation, version history, metadata updates (including deactivation and domain scope), variant listing, and variant weight/enabled-state changes. The admin web UI now provides signature creation/editing, new-version uploads, activation controls, and variant management. Service-level regression tests cover metadata updates and inactive signature creation. Variant deletion and end-to-end PostgreSQL application testing remain.

## Important operational note

The new startup validation means an existing deployment with no valid `SECRET_KEY` will no longer start from this branch. Configure a strong secret before testing the branch. Session cookies are Secure, so access the web UI over HTTPS. This is an intentional fail-safe, not a production migration performed by this project work.
