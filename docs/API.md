# OpenSigWeave management and engine API

Browser administration endpoints use the authenticated OIDC session and require the configured administrator group. State-changing browser requests require the session CSRF token in the `X-CSRF-Token` header. The rule-management and signature-catalog endpoints are connected to the administrator UI.

## Signature catalog

### List signatures

`GET /api/admin/signatures`

Returns logical signatures with their scope, domain, mode, active state and counts of versions and variants. It does not return signature HTML.

### Create a signature

`POST /api/admin/signatures`

Example request:

```json
{
  "name": "Default corporate signature",
  "domain_id": 1,
  "mode": "AUTO",
  "weight": 2,
  "html_content": "<table><tr><td>{{ first_name }} {{ last_name }}</td></tr></table>"
}
```

- `domain_id` may be `null` for a global signature.
- `mode` is one of `AUTO`, `PROFESSIONAL` or `INTERNAL`.
- `is_active` is optional and defaults to `true`.
- The initial HTML version is written to the configured signature file store; the database stores its generated version ID, relative path, SHA-256 hash and size.
- An initial variant is created in the same mode pool.
- HTML is sanitized with an allowlist before every version is written. Common email layout tags, table attributes and a constrained set of inline CSS properties are retained; scripts, event handlers, embedded active content, unsafe URL schemes and relative URLs are removed.
- Dynamic identity values are rendered with HTML autoescaping after sanitization.

### Update signature metadata

`PUT /api/admin/signatures/{signature_id}`

Example request:

```json
{
  "name": "Corporate signature",
  "domain_id": 1,
  "is_active": false
}
```

This endpoint changes the name, global/domain scope and active state. The signature mode is intentionally immutable after creation because it determines the variant pool and rule compatibility. Deactivation preserves versions and metadata but excludes the signature from selection.

### Add a version

`POST /api/admin/signatures/{signature_id}/versions`

Example:

```json
{
  "html_content": "<table><tr><td>Updated {{ first_name }}</td></tr></table>"
}
```

Version numbers are allocated while the signature row is locked. The file is written before the metadata transaction commits; if the database commit fails, the newly written file is removed.

### Add a variant

`POST /api/admin/signatures/{signature_id}/variants`

Example:

```json
{
  "pool_name": "AUTO",
  "weight": 1
}
```

The pool must match the signature's mode. Weights must be positive and determine relative selection probability.

### List and update variants

- `GET /api/admin/signatures/{signature_id}/variants` returns the signature's variants.
- `PUT /api/admin/signature-variants/{variant_id}` updates `weight` and `is_enabled`.

Example update:

```json
{
  "weight": 3,
  "is_enabled": true
}
```

### List versions

`GET /api/admin/signatures/{signature_id}/versions`

Returns version identifiers, version numbers, hashes, sizes, authors and creation timestamps. Signature HTML content is not returned by this endpoint.

## Signature rules

Rules are evaluated in descending priority order, with the rule ID as a stable tie-breaker. Conditions within a rule are combined using AND semantics. A rule with no conditions matches every sender.

### List rules

`GET /api/admin/rules`

Returns each rule, its action, optional signature target, enabled state and conditions.

### Create a rule

`POST /api/admin/rules`

Example request:

```json
{
  "name": "Sales team",
  "priority": 100,
  "action_mode": "PROFESSIONAL",
  "signature_id": null,
  "is_enabled": true,
  "conditions": [
    {"kind": "group_contains", "key": "", "value": "Sales"},
    {"kind": "attribute_equals", "key": "department", "value": "Sales"}
  ]
}
```

Supported condition kinds: `sender_equals`, `domain_equals`, `group_contains`, `claim_equals` and `attribute_equals`. `claim_equals` evaluates selected Authentik user fields (`username`, `email`, `name`, `is_active`, `uid`, `path`, `type`); `attribute_equals` evaluates the user's custom Authentik attributes. If a signature target is supplied, its mode must match the rule action mode.

### Update and delete rules

- `PUT /api/admin/rules/{rule_id}` replaces the rule and its conditions transactionally.
- `DELETE /api/admin/rules/{rule_id}` deletes the rule and its conditions.

## Rspamd engine endpoint

`GET /api/signature/{email}`

Requires the `X-Engine-Key` header. The endpoint returns the rendered HTML and the domain's reply/whitespace options. Responses are marked `Cache-Control: no-store`.

Rspamd may send one `X-OpenSigWeave-Mode` header with one of these values: `AUTO`, `PROFESSIONAL`, `INTERNAL` or `NONE`. The Lua adapter accepts this header only on authenticated submissions, rejects duplicate or unsupported values, forwards a valid value to the engine API and removes the control header from the message.

If no valid mode header is present, the adapter also checks decoded HTML text parts for an Outlook marker. Supported forms are `<!-- OpenSigWeave:MODE=PROFESSIONAL -->` and an element carrying `data-opensigweave-mode="PROFESSIONAL"`. Replace the mode with `AUTO`, `INTERNAL` or `NONE` as required. Place the marker in the newest message section; markers found only after a recognized quoted-reply boundary are ignored. On replies where no quote boundary can be identified, marker detection fails closed. The marker is stripped from the outgoing HTML before the signature is inserted. A valid explicit mode header takes precedence over the HTML marker.

## Resolution precedence

1. An administrator-disabled user override returns no signature.
2. An explicit authenticated message-mode header overrides personal overrides and rules.
3. Without an explicit mode header, an enabled personal override is used only when the domain allows overrides.
4. Otherwise, the highest-priority matching persisted rule determines the mode and optional signature target.
5. If no rule matches, the default mode is `AUTO`; the legacy domain template is used if no catalog variant is available in that pool.

The `NONE` mode intentionally returns an empty signature.

## Current limitations

- Variant deletion and logical signature deletion are not implemented. Prefer deactivation so rule references and historical versions remain intact.
- Outlook marker detection uses decoded HTML text parts and strips the marker before injection. Client-specific preservation of the marker and all reply-format variants still require tests with real Outlook versions.
- Rspamd MIME mutation and DKIM processing order still require isolated end-to-end verification.
- The current selection and rule tests cover SQLite. The CI pipeline separately verifies PostgreSQL and SQLite schema migrations. Concurrent PostgreSQL selection tests and full application-level API tests remain pending.
