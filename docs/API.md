# OpenSigWeave management and engine API

This document describes the API implemented on the standalone preview branch. Browser management endpoints use the OIDC session. Administrator endpoints require membership in the `signature-admins` group. Personal signature endpoints require `signature-users` or `signature-admins`.

State-changing browser requests require the session CSRF token in the `X-CSRF-Token` header. The web UI attaches it to its requests.

## Authentication and authorization

- `GET /login`: starts the OIDC Authorization Code flow.
- `GET /auth`: OIDC callback. The UserInfo response must include a subject, a valid email, `email_verified: true` (JSON boolean), and a `groups` array.
- `POST /logout`: clears the local session and returns the Authentik logout URL.

The OIDC discovery URL and client credentials are configurable. However, the current implementation still uses Authentik-specific logout handling and an Authentik API-backed employee-attribute provider. Authentik is therefore the supported complete configuration for this preview; a generic OIDC-only provider is not yet fully supported.

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

Returns logical signatures with scope, domain, mode, active state and version/variant counts. It does not return signature HTML.

### Create a signature

`POST /api/admin/signatures`

Example:

```json
{
  "name": "Default corporate signature",
  "domain_id": null,
  "mode": "AUTO",
  "weight": 2,
  "html_content": "<table><tr><td>{{ first_name }} {{ last_name }}</td></tr></table>"
}
```

- `domain_id` may be `null` for a global signature.
- `mode` is `AUTO`, `PROFESSIONAL` or `INTERNAL`.
- `is_active` defaults to `true`.
- The HTML is sanitized and written to the signature file store.
- PostgreSQL stores the version identifier, relative path, SHA-256 hash, size and metadata.
- An initial variant is created in the same mode pool.

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

This changes the name, global/domain scope and active state. The mode is immutable after creation. Deactivation preserves the versions and metadata but excludes the signature from selection.

### Versions and variants

- `GET /api/admin/signatures/{signature_id}/versions`: list version metadata, hashes and timestamps.
- `POST /api/admin/signatures/{signature_id}/versions`: create a new HTML version.
- `GET /api/admin/signatures/{signature_id}/variants`: list variants.
- `POST /api/admin/signatures/{signature_id}/variants`: add a variant to the matching mode pool.
- `PUT /api/admin/signature-variants/{variant_id}`: update variant weight and enabled state.

Weights must be positive. Version files are immutable and integrity-checked when read. Prefer deactivation to deletion; catalog deletion is not implemented.

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

`claim_equals` currently evaluates selected Authentik user fields (`username`, `email`, `name`, `is_active`, `uid`, `path`, `type`). `attribute_equals` evaluates custom Authentik attributes. A selected signature's mode must match the rule's action mode.

## Rspamd engine endpoint

`GET /api/signature/{email}`

Requires the `X-Engine-Key` header. The key must match the application's `ENGINE_API_KEY`. The response includes rendered HTML and domain policy flags and is marked `Cache-Control: no-store, private`.

The sender email is normalized and validated. The Rspamd adapter sends the SMTP envelope sender; it also requires an authenticated SMTP user to be present in the Rspamd task. This alone does not prove that the authenticated mailbox is authorized to use that envelope sender. Mailcow/Postfix sender verification and explicit alias/delegated-send-as permissions must remain enabled.

### Message mode

Rspamd may send one `X-OpenSigWeave-Mode` header with one of these values:

- `AUTO`
- `PROFESSIONAL`
- `INTERNAL`
- `NONE`

The adapter accepts the header only for authenticated submissions, rejects duplicate or unsupported values, forwards a valid value to the engine API and removes the control header from the message.

If no valid header is present, the adapter checks decoded HTML text parts for an Outlook marker such as:

```html
<!-- OpenSigWeave:MODE=PROFESSIONAL -->
```

or an element with `data-opensigweave-mode="PROFESSIONAL"`. Markers found only after recognized quoted-reply boundaries are ignored. On replies where no quote boundary can be identified, marker detection fails closed. The marker is removed before signature insertion. A valid explicit mode header takes precedence over the HTML marker.

## Resolution precedence

1. An administrator-disabled user override returns no signature.
2. An explicit authenticated message-mode header overrides personal overrides and rules.
3. Without an explicit mode header, an enabled personal override is used only when the domain allows overrides.
4. Otherwise, the highest-priority matching rule determines the mode and optional signature target.
5. If no rule matches, the default mode is `AUTO`; the legacy domain template is used if no catalog variant is available in that pool.
6. `NONE` returns an empty signature.

## Current limitations and verification status

- The current Rspamd adapter deliberately leaves every top-level `multipart/*` message unchanged. This includes common `multipart/alternative` messages and messages with attachments. The current rewrite path is limited to non-multipart messages.
- Actual Rspamd/Mailcow processing order and DKIM signing order have not been validated in an isolated end-to-end mail environment.
- Outlook marker preservation and reply behavior need testing with real client versions.
- SMTP envelope-sender authorization must be enforced by Mailcow/Postfix; `task:get_user()` is not sufficient authorization by itself.
- Docker CI validates image build, Compose startup, PostgreSQL migrations, healthchecks and an authenticated engine API request. It does not perform an OIDC browser login or a full SMTP delivery test.
- CSP remains report-only pending real-browser testing.
