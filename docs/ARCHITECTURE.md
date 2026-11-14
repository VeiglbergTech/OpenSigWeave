# OpenSigWeave – architecture and implementation boundaries

**Document status:** Current preview architecture with explicit future boundaries  
**Branch:** `architecture/refactor-foundation`  
**Last reviewed:** 2026-11-12

## 1. Product boundary

OpenSigWeave is developed first as an independent, self-hosted application. Its standalone deployment must not require Mailcow or modify a mail server.

The intended long-term product boundary is:

- **OpenSigWeave core:** signature catalog, versions, variants, rules, rendering, identity abstraction, management API and web UI.
- **Mail adapters:** separate integrations such as Rspamd. They consume a stable engine API and must not make the core depend on a particular mail server.
- **Native Mailcow integration:** a separate future project. It may add Mailcow-specific authentication, user discovery and UI integration without requiring changes to the standalone core.

Managed identity providers are the default. New installations use `OIDC_ENABLED=false`, configure an independent local administrator emergency account and create providers in the portal. Each provider's settings and secrets live in `.idp/{provider-id}/.env`. Domains select a login provider and a profile-attribute provider independently. The previous root-level OIDC configuration remains available only as an opt-in compatibility path with `OIDC_ENABLED=true`.

## 2. Current runtime components

### Web application

- FastAPI application served by Uvicorn on TCP port 8085 inside the container.
- Server-rendered Jinja templates and same-origin frontend assets, with the pinned Alpine CSP build still loaded from a CDN.
- The login page presents a separate sign-in box for each configured Authentik provider and an email/password form for LDAP and the local administrator. The selected OIDC callback validates the returned email domain against the provider assignment.
- Browser session authorization based on the `signature-users` and `signature-admins` groups.
- CSRF protection for state-changing browser requests.
- Secure session cookies and trusted-host validation.

### Identity and attributes

Managed identity-provider configuration is stored outside PostgreSQL under `.idp/{provider-id}/.env`; database rows contain provider metadata and per-domain assignments only. Provider secrets are not returned by the management API.

**Login provider and profile provider are separate domain settings.** The login route extracts the email domain and selects its assigned provider. LDAP / Active Directory performs an exact email search with a read-only service bind, then verifies the password with a user-DN bind over LDAPS. Authentik uses OIDC Authorization Code with its own discovery URL, client credentials, scopes and exact callback URI `/auth/{provider-id}`. OIDC callbacks require a stable subject, a verified email (email_verified must be the JSON boolean true) and a groups array, then re-check the domain's current provider assignment.

The profile provider is used independently by the portal and signature engine. The Authentik API provider and LDAP service-bind provider retrieve active user attributes and selected claims. Successful values are cached per provider ID and normalized email. A confirmed missing or inactive account is an authoritative empty result and clears that provider's cached attributes; technical lookup failures fall back to the last successful cache.

The local administrator account uses a salted scrypt hash and remains independent of external provider availability. New installations default to managed providers; root-level global OIDC is retained only for explicit compatibility mode. Managed OIDC logout uses the provider's discovery-based HTTPS end-session endpoint and an optional post-logout redirect URI. The legacy global OIDC compatibility path still uses its Authentik-specific logout URL.

### Signature and rule services

The implemented services cover:

- Signature modes: `AUTO`, `PROFESSIONAL`, `INTERNAL`, `NONE`.
- Signature catalog metadata and active state.
- Name-based signature directories at `signatur/{Signaturname}/signature.html`, with immutable UUID-addressed snapshots in each directory's `.versions/` subdirectory. PostgreSQL retains stable internal signature IDs, version metadata, SHA-256 hashes and integrity checks; renaming a signature moves its directory without changing its ID.
- Weighted variants and selection state to avoid immediate repetition for a sender/mode pool.
- Rules with priorities and AND-combined conditions for sender, domain, groups, claims and attributes.
- HTML sanitization before storage and rendering with automatic escaping of dynamic identity values.

### Persistence and file storage

PostgreSQL is the supported standalone deployment database. SQLAlchemy manages relational records and Alembic manages schema migrations. SQLite remains available for compatibility and unit-test scenarios.

PostgreSQL stores domain and override configuration, signature metadata, version manifests, variants, rules, selection state and shared rate-limit buckets. Signature HTML content is stored in a separate filesystem root, configured through `SIGNATURE_STORAGE_PATH`. Each logical signature has a readable `signatur/{Signaturname}/signature.html` current file; immutable snapshots are retained below `signatur/{Signaturname}/.versions/` so historical version metadata remains valid.

The application image stores files in `/data/signatures`; Compose persists this path in the `opensigweave-signatures` named volume. PostgreSQL uses the separate `opensigweave-postgres-data` volume. Both volumes must be backed up and restored together.

### Rspamd adapter

The Lua adapter is a separate integration. It:

- Requires an authenticated Rspamd task and uses the SMTP envelope sender.
- Calls the engine API with `X-Engine-Key`.
- Supports a validated `X-OpenSigWeave-Mode` header and Outlook HTML markers.
- Uses bounded HTTP request time and response-size settings.
- Deliberately leaves every top-level `multipart/*` message unchanged.

The current rewrite path is limited to non-multipart messages. No production mail flow or DKIM ordering is certified.

## 3. Request and authorization boundaries

### Browser identity

The browser login page lists one sign-in box per configured Authentik OIDC provider. Selecting a box starts that provider's Authorization Code flow; the callback verifies the email and checks that its domain is still assigned to the selected provider. LDAP users and the local administrator use the email/password form. LDAP routing is selected by the email domain. The legacy global OIDC callback at `/auth` is registered only when `OIDC_ENABLED=true`.

When legacy global OIDC is enabled, the canonical hostname is derived from its `OIDC_REDIRECT_URI`. In managed-provider mode, `APP_ALLOWED_HOSTS` must explicitly list the allowed portal hostname(s). Wildcards are rejected in both modes.

### Portal permissions

- `signature-users`: normal portal and personal-signature access.
- `signature-admins`: administrator APIs and management operations.

The server checks these permissions in route handlers. Hiding a menu item in the browser is not an authorization control.

### Engine identity

The Rspamd adapter authenticates to `GET /api/signature/{email}` with `X-Engine-Key`. The key is configured independently from the session secret and is never embedded in the tracked Lua source.

The Lua adapter requires `task:get_user()`, but that only indicates an authenticated SMTP session. It does not prove that the authenticated mailbox may send as the envelope sender. Mailcow/Postfix sender verification and explicit alias/delegated-send-as permissions must enforce that relationship before the signature hook.

## 4. Standalone Docker deployment

The root `compose.yaml` starts:

- `postgres`: PostgreSQL 17 Alpine with a healthcheck and a persistent named volume.
- `opensigweave`: the application image, built from the root Dockerfile.

The application container:

- Runs as UID/GID 10001, not root.
- Uses a read-only root filesystem and a bounded `/tmp` tmpfs.
- Drops all Linux capabilities and enables `no-new-privileges`.
- Waits for PostgreSQL health and applies Alembic migrations before starting Uvicorn.
- Exposes port 8085 only on host loopback by default.
- Persists signature assets in a separate named volume.

The deployment requires a reverse proxy for browser-facing HTTPS. A proxy on the same host can use `http://127.0.0.1:8085`. A proxy in a separate container or host requires a deliberately configured private network path and firewall restriction; do not expose the application port to the public internet.

The Compose stack does not install Rspamd, change Mailcow, or configure a mail server.

## 5. Implemented data model

Current persisted concepts include:

- Domains and their injection/override policy.
- Personal user overrides.
- Logical signatures and their scope/mode/active state.
- Immutable signature versions and file integrity metadata.
- Signature variants and weights.
- Signature rules and typed conditions.
- Per-sender variant-selection state.
- Shared database-backed rate-limit buckets.
- Identity-provider metadata and separate per-domain login/profile-provider associations.
- Provider-scoped cached identity attributes keyed by normalized email.

An independent audit-log subsystem, SCIM provisioning and nested LDAP group expansion are not part of the current preview.

## 6. Security and operational controls

Current implementation includes:

- Strong non-placeholder startup validation for `SECRET_KEY` and `ENGINE_API_KEY`.
- HTTPS-only OIDC discovery and Authentik API URL validation.
- TLS certificate verification for Authentik API requests and LDAPS connections.
- Per-provider OIDC callback validation and per-domain login-provider revalidation.
- Secure, HttpOnly, SameSite session cookies with a one-hour lifetime.
- Session-bound CSRF checks for state-changing browser requests.
- Trusted Host validation using explicit allowed hostnames; legacy OIDC also validates its configured callback hostname.
- Security response headers and a global request-body size limit.
- Server-side HTML sanitization, sandboxed previews and autoescaped template variables.
- File-store path/symlink checks, atomic writes and SHA-256 integrity validation.
- Database-backed rate limiting shared across workers.
- A non-root, read-only Docker application container.
- CI dependency auditing and full Git-history secret scanning.

The Content Security Policy is report-only until full browser validation. These controls are not a substitute for a dynamic penetration test or production deployment review.

## 7. Mail-flow constraints

Before enabling Rspamd in any real mail flow, a disposable integration environment must verify:

1. The SMTP envelope sender is authorized for the authenticated mailbox by Mailcow/Postfix.
2. The Lua module runs at the intended processing stage.
3. Signature mutation occurs before DKIM signing.
4. The final delivered message passes DKIM validation.
5. Repeated processing does not inject duplicate signatures.
6. Plain text and HTML single-part messages are preserved correctly.
7. Multipart messages, attachments, inline Content-IDs and transfer encodings remain unchanged until a MIME-aware rewrite implementation is tested.

The current adapter intentionally skips multipart messages. Do not work around this by adding raw string-based multipart reconstruction.

## 8. Near-term development scope

The first testable standalone revision should focus on:

- Reliable Docker installation and upgrade instructions.
- Domain-routed Authentik OIDC and LDAP/Active Directory authentication, with an independent local administrator emergency account.
- Domain, signature, variant and rule workflows.
- PostgreSQL persistence and signature-file persistence.
- Browser-level verification and CSP report review.
- An isolated Rspamd test only after the portal works.

Do not start native Mailcow UI integration in this repository. That work belongs in a separate project after the standalone product is stable.

## 9. Deferred design work

- Live interactive verification against real Authentik and Active Directory servers.
- Nested LDAP group expansion and SCIM user/group provisioning.
- Native Mailcow authentication, user synchronization and UI integration.
- Parser-backed multipart MIME mutation.
- Full audit logging and retention policy.
- Production-grade backup/restore automation and deployment orchestration.
- Dynamic penetration testing, load testing and operational SLOs.
