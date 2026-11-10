# OpenSigWeave management API

The following endpoints are available to authenticated administrators. They are the first API surface for the signature catalog; the web administration UI has not yet been connected to them.

All routes use the existing browser session and require the configured administrator group. State-changing requests also require the session CSRF token in the `X-CSRF-Token` header.

## List signatures

`GET /api/admin/signatures`

Returns logical signatures with their scope, domain, mode, active state and counts of versions and variants. It does not return signature HTML.

## Create a signature

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
- The initial HTML version is written to the configured signature file store; the database stores its generated version ID, relative path, SHA-256 hash and size.
- An initial variant is created in the same mode pool.
- Dynamic identity values are rendered with HTML autoescaping.

## Add a version

`POST /api/admin/signatures/{signature_id}/versions`

Example:

```json
{
  "html_content": "<table><tr><td>Updated {{ first_name }}</td></tr></table>"
}
```

Version numbers are allocated while the signature row is locked. The file is written before the metadata transaction commits; if the database commit fails, the newly written file is removed.

## Add a variant

`POST /api/admin/signatures/{signature_id}/variants`

Example:

```json
{
  "pool_name": "AUTO",
  "weight": 1
}
```

The pool must match the signature's mode. Weights must be positive and determine relative selection probability.

## List versions

`GET /api/admin/signatures/{signature_id}/versions`

Returns version identifiers, version numbers, hashes, sizes, authors and creation timestamps. Signature HTML content is not returned by this endpoint.

## Current limitations

- The UI does not yet expose these routes.
- There are not yet update/deactivate/delete endpoints for signatures or variants.
- Rule persistence models and the pure rule evaluator exist, but rule-management endpoints are not yet implemented.
- Explicit SMTP-header and Outlook marker modes are not yet passed through the Rspamd adapter.
- The Rspamd engine currently resolves the `AUTO` pool; `PROFESSIONAL` and `INTERNAL` pools are not yet selectable from message context.
