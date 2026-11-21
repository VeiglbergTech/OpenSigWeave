# OpenSigWeave management and engine API

This document describes the API currently implemented on the `dev` branch. Browser management endpoints use a signed, secure session established by either OIDC or the local administrator login. Administrator endpoints require membership in the `signature-admins` group or an explicitly configured machine management key. Personal signature endpoints require a browser session with `signature-users` or `signature-admins`.

State-changing browser requests require the session CSRF token in the `X-CSRF-Token` header. The web UI attaches it to its requests. Unattended administrator API calls can instead use the optional management credential described below.

## Unattended administrator automation

Set `MANAGEMENT_API_KEY` to a newly generated secret of at least 32 characters. This setting is optional; empty or unset disables management-key authentication. It must differ from both `ENGINE_API_KEY` and `SECRET_KEY`; obvious placeholder values are rejected at startup. Restart the application after changes.

Send `X-Management-Key: <secret>` on the existing administrator endpoints: `/api/admin/*` and `/domains/*`. This is a **full administrator credential**, granting read and write access including imports, provider configuration, rule changes and deletions. Use it only for trusted automations on HTTPS connections with restricted access. Store it in a secret manager, never in Git, URLs, browser JavaScript, command history or request logs. Rotate by replacing the environment variable and restarting. There are no granular scopes or key-management UI in v1.0.

For example, with the token loaded into the calling process's `MANAGEMENT_API_KEY` environment variable:

```bash
curl --fail-with-body --silent --show-error \
  -H "X-Management-Key: ${MANAGEMENT_API_KEY}" \
  https://signature.example.org/api/admin/signatures
```

Valid management-key requests to these administrator paths do not need session cookies or CSRF tokens. Browser sessions **still** require `X-CSRF-Token` for changes unless a valid management key is explicitly supplied. An invalid token never authorizes an operation. Management keys do not authenticate `/api/my-signature`, browser pages, login, or `/api/v1/engine/*`. The mail-adapter `ENGINE_API_KEY` cannot access management routes. Key-authenticated responses are non-cacheable.

For machine administration, prefer these existing REST resources over a duplicate CLI: domain management, signature catalog and versions, signature groups and memberships, rules, scheduling, branding, overrides, provider configuration, image assets, and selective import/export. The separate operator CLI supports encrypted full backups/restores; details are in [CLI/API v1.0 decisions](CLI_API_V1.md).

## Read-only system diagnostics

`GET /api/admin/system-status` is accessible only to administrator sessions or a
valid machine management key. It returns application version, UTC timestamp,
database and signature storage status, aggregate configuration record counts,
and non-secret flags for operator-managed configuration. Responses are
non-cacheable. It never returns credentials, provider configuration, user
identities or filesystem paths, writes no probe files and contacts neither
identity providers nor mail servers. Degraded status does not expose exception
details. The browser **System** tab shows the snapshot and a refresh control.
Full encrypted backup/restore, deployment secrets and MTA diagnostics remain
operator-only.

## Authentication and authorization

- `GET /login`: renders the local administrator form, LDAP email/password login and buttons for configured managed OIDC providers.
- `POST /login`: authenticates the local administrator or the LDAP provider assigned to the submitted email domain; for a managed OIDC domain it starts that provider's Authorization Code flow. Requires the one-time login CSRF token.
- `GET /login/oidc/{provider_id}`: starts managed OIDC login for the selected provider.
- `GET /auth/{provider_id}`: managed OIDC callback. UserInfo must include a subject, valid email, `email_verified: true` (JSON boolean) and a `groups` array; the email domain must still be assigned to that provider.
- `POST /logout`: clears the session; managed OIDC sessions use the provider's HTTPS end-session endpoint when available, otherwise the user returns to `/login`.

Managed identity providers are configured in the administrator portal and assigned independently per email domain: LDAP uses LDAPS password authentication; Authentik/generic OIDC uses managed callbacks at `/auth/{provider_id}`. The local administrator is an independent emergency account and is assigned `signature-admins` and `signature-users`. All installations require `LOCAL_ADMIN_EMAIL`, a scrypt `LOCAL_ADMIN_PASSWORD_HASH` and explicit `APP_ALLOWED_HOSTS`. There is no global OIDC client or `/auth` callback.

For Authentik, the default `profile` scope mapping provides group membership. The custom `opensigweave` scope mapping must provide `email_verified` as a JSON boolean and an `attributes` object. Authentik 2025.10 and newer no longer assert email verification by default; see the README for the required mapping and user attribute configuration.

The application recognizes these group names:
- `signature-users`: normal portal access and personal signature endpoints.
- `signature-admins`: administrator access.

## Domain and personal override endpoints

The following endpoints require an authenticated administrator unless noted otherwise:

- `GET /domains/`
- `POST /domains/`
- `PUT /domains/{domain_id}`
- `DELETE /domains/{domain_id}`
- `GET /api/admin/overrides/{domain_name}`
- `POST /api/admin/overrides`
- `DELETE /api/admin/overrides/{override_id}`

Personal override endpoints require membership in `signature-users` or `signature-admins`:

- `GET /api/my-signature`
- `POST /api/my-signature`
- `DELETE /api/my-signature`

Personal override changes also respect the domain's `allow_overrides` policy. An administrator-disabled override cannot be re-enabled or deleted by the user.

## Signature catalog

All catalog endpoints require the administrator group.

### List signatures

`GET /api/admin/signatures`

Returns logical signatures with scope, domain assignments, active state and version count. It does not return signature HTML.

### Create a signature

`POST /api/admin/signatures`

Example:

```json
{
  "name": "Default corporate signature",
  "domain_id": null,
  "html_content": "<table><tr><td>{{ first_name }} {{ last_name }}</td></tr></table>",
  "is_active": true
}
```

- `domain_id` may be `null` for a global signature.
- `is_active` defaults to `true`.
- The submitted HTML source is stored as the immutable version source; it is allowlist-sanitized before preview and delivery.
- PostgreSQL stores the version identifier, relative path, SHA-256 hash, size and metadata.

Dynamic identity values are rendered with HTML autoescaping.

### Update signature metadata

`PUT /api/admin/signatures/{signature_id}`

Example:

```json
{
  "name": "Corporate signature",
  "domain_id": 1,
  "is_active": false
}
```

This changes the name, global/domain scope and active state. Deactivation preserves the versions and metadata but excludes the signature from selection.

### Versions

- `GET /api/admin/signatures/{signature_id}/versions`: list version metadata, hashes and timestamps.
- `POST /api/admin/signatures/{signature_id}/versions`: create a new HTML version.
- `GET /api/admin/signatures/{signature_id}/versions/{version_id}`: preview immutable HTML without making it active.
- `POST /api/admin/signatures/{signature_id}/versions/{version_id}/restore`: create a new active version from historical content, preserving the old history.
Version files are immutable and integrity-checked when read. Restoring an old version creates a **new** active version rather than replacing any historical revision. Prefer deactivation to deletion; catalog deletion is not implemented.

### Signature image assets

- `POST /api/admin/signature-assets`: administrator-only, CSRF-protected image upload (a valid machine management key may also authenticate). PNG, JPEG and WebP are supported; GIF is supported for compatibility. Maximum file size: 2 MiB. The response contains an immutable `/signature-assets/<sha256>.<ext>` URL.
- `GET /signature-assets/{asset_name}`: publicly readable immutable image, required so external mail recipients can load images without a browser session. Configure the reverse proxy to permit anonymous GET for only this path while keeping upload and other administrator endpoints protected.

The editor stores absolute HTTPS asset URLs in signature HTML; temporary `blob:` and embedded `data:` images are not a reliable delivery format. Assets reside in the persistent signature volume and are shared by older versions. Deleting an HTML image reference does not purge a shared image. See [README image deployment requirements](../README.md#signature-images-and-publicly-accessible-asset-urls).

## Signature rules

All rule endpoints require the administrator group:

- `GET /api/admin/rules`
- `POST /api/admin/rules`
- `PUT /api/admin/rules/{rule_id}`
- `DELETE /api/admin/rules/{rule_id}`

Rules are evaluated in descending priority order, with a stable ID tie-breaker. Conditions within one rule use AND semantics. A rule without conditions matches every sender.

Supported condition kinds:

- `sender_equals`
- `domain_equals`
- `group_contains`
- `claim_equals`
- `attribute_equals`

`claim_equals` currently evaluates selected Authentik user fields (`username`, `email`, `name`, `is_active`, `uid`, `path`, `type`). `attribute_equals` evaluates custom Authentik attributes. Rule actions are `SIGNATURE`, `GROUP` or `NONE`. `SIGNATURE` requires a signature target, `GROUP` requires a group target, and `NONE` deliberately suppresses injection.

## Adapter-neutral engine endpoint

`GET /api/v1/engine/signatures/{email}` is the versioned, mail-server-neutral engine contract. It accepts the same authentication and selection headers as the Rspamd integration below and returns the same rendered signature and policy fields. New adapters MUST use this versioned path; the older `GET /api/signature/{email}` path remains as a backwards-compatible Rspamd alias for existing deployments.

The shared response shape is JSON:

```json
{
  "html": "<p>Rendered signature</p>",
  "inject_on_replies": false,
  "trim_whitespace": true,
  "strip_device_signatures": false
}
```

The endpoint is deliberately transport-neutral: it does not parse or mutate RFC 5322/MIME messages, inspect a mail-server queue, or decide where DKIM signing occurs. The adapter owns those responsibilities and MUST preserve message structure, enforce sender authorization, avoid duplicate insertion and run before DKIM signing.

## Stalwart MTA hook endpoint (experimental)

`POST /api/v1/adapters/stalwart/mta-hook`

Stalwart calls this endpoint at the SMTP DATA stage. Authenticate requests with `Authorization: Bearer <STALWART_HOOK_TOKEN>`; configure the token as an environment variable and do not store it in tracked configuration. The endpoint ignores unauthenticated sessions and never injects signatures into unauthenticated inbound mail. The envelope sender is taken from Stalwart's trusted SMTP envelope, not from the `From` message header. Stalwart must enforce authenticated sender/alias authorization independently.

The adapter uses the shared signature engine and returns Stalwart's `accept` action with optional `replaceContents`, `addHeader` and `deleteHeader` modifications. It currently supports parser-based text/plain and text/html MIME part updates, including multipart alternatives. Signed/encrypted messages and attachments are left unchanged. This integration is experimental; see `docs/ADAPTERS.md` for limitations and release gates.

## Rspamd engine endpoint

`GET /api/v1/engine/signatures/{email}` (recommended versioned path; mail-server-neutral contract). The older `GET /api/signature/{email}` route remains available as a backwards-compatible alias for existing Rspamd installations.

Requires the `X-Engine-Key` header. The key must match the application's `ENGINE_API_KEY`. The response includes rendered HTML and domain policy flags and is marked `Cache-Control: no-store, private`.

The sender email is normalized and validated. The Rspamd adapter sends the SMTP envelope sender; it also requires an authenticated SMTP user to be present in the Rspamd task. This alone does not prove that the authenticated mailbox is authorized to use that envelope sender. Mailcow/Postfix sender verification and explicit alias/delegated-send-as permissions must remain enabled.

### Per-message signature selector

The engine accepts an optional concrete-signature selector through `X-OpenSigWeave-Signature` and `X-OpenSigWeave-Signature-Code`. The Rspamd/Postfix integrations may extract the same selector from the supported body marker. Mode-based selectors are retired; the deprecated mode header is consumed by adapters for cleanup only and is not forwarded to or evaluated by the engine.

## Resolution precedence

1. An administrator-disabled user override returns no signature.
2. A validated concrete-signature marker selects that signature.
3. Otherwise, the highest-priority active schedule for the sender's domain is applied. Domain-specific schedules win equal-priority ties against all-domain schedules.
4. Without an active schedule, an enabled personal override is used only when the domain allows overrides.
5. Otherwise, the highest-priority matching rule selects a direct signature, a signature group or no signature.
6. If no rule matches, the domain template is used.

## Current limitations and verification status

- The Rspamd adapter rewrites supported `text/plain` and `text/html` leaves in parsed `multipart/alternative`, `multipart/mixed` and `multipart/related` structures, including nested MIME. Binary attachments and inline CID parts are copied unchanged. Signed/encrypted, attached `message/rfc822`, malformed or unsupported MIME structures are skipped as a whole. Lua fixtures re-parsed with Python's MIME parser provide automated coverage; real Mailcow/Rspamd SMTP integration and final DKIM validation remain unverified. See [Mailcow/Rspamd staging guidance](MAILCOW_RSPAMD_INTEGRATION.md).
- Actual Rspamd/Mailcow processing order and DKIM signing order have not been validated in an isolated end-to-end mail environment.
- Outlook marker preservation and reply behavior need testing with real client versions.
- SMTP envelope-sender authorization must be enforced by Mailcow/Postfix; `task:get_user()` is not sufficient authorization by itself.
- Application CI validates image build, Compose startup, migrations, Chromium administration workflows, a real Docker backup/fresh restore and API/regression tests. Separate Postfix-Milter compatibility CI performs an isolated SMTP delivery through Postfix with a mock engine; it does **not** exercise live Mailcow/Rspamd, real OIDC/LDAP or the production DKIM signing chain.
- CSP remains report-only pending broader browser/deployment acceptance. The CI Chromium workflow records policy-violation events and currently observes none across the exercised administration/editor paths.


## Signature groups

Administrator-only endpoints:

- `GET /api/admin/signature-groups` — list groups, domain scopes and members.
- `GET /api/admin/signature-groups/{group_id}/preview?domain_id={id}` — render a deterministic preview using the first eligible signature ID. Requires administrator access and never reads or changes per-user rotation state.
- `POST /api/admin/signature-groups` — create a group. Body: `{"name":"Corporate","slug":"corporate","domain_ids":[],"is_active":true}`. Omit slug to derive it from the name. An empty domain list means all domains.
- `PUT /api/admin/signature-groups/{group_id}` — update group metadata and domain scope.
- `DELETE /api/admin/signature-groups/{group_id}` — delete an unreferenced group. Rules referencing it must be updated first.
- `POST /api/admin/signature-groups/{group_id}/members` — add a signature with `signature_id`, positive `weight` and `is_enabled`.
- `PUT /api/admin/signature-groups/{group_id}/members/{signature_id}` — update membership weight or enabled state.
- `DELETE /api/admin/signature-groups/{group_id}/members/{signature_id}` — remove a membership.

Rules accept `action_mode: "GROUP"` together with `group_id`; `signature_id` must be null. A group tag in signature HTML uses `{{ group:slug }}`. Repeated tags for the same slug reuse one selection during that message. Selection state requires a provider-namespaced immutable identity: Authentik user `pk` or Active Directory `objectGUID`; email is never used as the group rotation key.

Migration `0014_retire_signature_modes` converts installed legacy pool rules to ordinary signature groups while retaining historical storage columns/tables only for database upgrade and rollback compatibility. Existing administrator-assigned domain scopes on compatibility groups are preserved. Current signature, rule and scheduling requests do not accept retired mode/variant selectors; legacy format-v1 imports are validated separately, and modern exports omit retired fields.


## Scheduling

Administrator-only endpoints:

- `GET /api/admin/schedules`
- `POST /api/admin/schedules`
- `PUT /api/admin/schedules/{schedule_id}`
- `DELETE /api/admin/schedules/{schedule_id}`

A schedule targets exactly one active signature or one active signature group. It may be scoped to one domain or to all domains. All-domain schedules require a globally scoped target. The time window is stored as timezone-aware UTC and uses `starts_at <= now < ends_at`. Overlapping schedules are resolved by descending `priority`; at equal priority a domain-specific schedule beats an all-domain schedule, then the most recent start time and stable ID break ties.

The web UI accepts browser-local date/time values and converts them to UTC before submission. The initial implementation supports explicit date ranges; recurring annual/weekly schedules are not part of this contract.

## Branding

Administrator-only endpoints:

- `GET /api/admin/branding`
- `PUT /api/admin/branding`

Branding stores the application name, six-digit hex accent color, optional PNG logo and optional PNG/ICO favicon in PostgreSQL. Logo uploads are limited to 512 KiB and favicon uploads to 256 KiB; the JSON request has a separate bounded 2 MiB request budget to account for base64 overhead. File signatures are checked server-side. Public assets are served through `/branding/logo.png` and `/branding/favicon`. Clearing a custom asset falls back to the bundled OpenSigWeave logo.
