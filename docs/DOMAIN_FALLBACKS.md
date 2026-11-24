# Domain-specific fallback values

Domain Management → Settings → **Fallback Values** contains an expandable list
of optional values for each domain: `first_name`, `last_name`, `title`
(position), `phone`, `company`, `department`, `location`, `mobile`
and `website`. The domain's configured values live in the `domains.fallback_values`
JSON column. The database migration initializes old domains with `{}`.

## Rendering semantics

1. For each recognized placeholder, the nonblank value returned by the configured
   identity provider takes precedence. Values from normalized identity attributes,
   raw LDAP/Authentik/OIDC attributes and claims are considered.
2. If a field is absent or whitespace-only, OpenSigWeave uses that domain's
   fallback if provided.
3. Otherwise, the field becomes the empty string. There is no longer an
   implicitly generated first name derived from the sender's local part.
4. `email` and `domain_name` always come from the verified engine request and
   cannot be configured as fallbacks. All values remain autoescaped in HTML.
5. The merged values are only passed into template rendering, including nested
   group templates and scheduled signatures. They are not stored in the identity
   cache and cannot be used as synthetic rule groups or authorization claims.

Known users with incomplete profiles and unknown users share the same fallback
logic. When a domain has at least one fallback field and a matched rule selects
a signature group, an unknown user without an immutable provider principal can
rotate group members under a stable SHA-256 sender-scoped key. Known principals
and the existing no-repeat group-selection algorithm remain unchanged. A domain
without any fallback values keeps the previous group eligibility behavior.

## Identity provider errors

Default (fail-open): provider request failures use the existing provider cache,
or an empty profile when there is no cache. Per-domain fallbacks can fill those
empty fields during rendering.

Set `IDENTITY_LOOKUP_FAIL_CLOSED=true` in the application environment and
restart the application to require a successful configured live LDAP or
Authentik API lookup for signature injection. Provider unavailability,
configuration errors or failed network requests then produce a safe
`IDENTITY_PROVIDER_UNAVAILABLE` HTTP **503** and do not silently use fallback
values. Submission fail-closed or fail-open handling still depends on the mail
adapter / Milter policy; the setting does not itself control the SMTP server.

A confirmed successful LDAP/Authentik lookup with no matching account is **not**
an infrastructure failure and can use domain fallbacks. Missing profile fields
also never cause a 503.

**Generic OIDC limitation:** OIDC is not a directory query API. OpenSigWeave
uses the user's claims previously captured during a verified OIDC login, with
a 24-hour cache lifetime. The mail-time signature engine cannot infer a live
OIDC outage from an absent/expired cache, and therefore does not claim
fail-closed detection of a live OIDC network outage. An Authentik provider
configured for live attribute lookup does have live-outage detection.

## API compatibility

The admin `GET /domains/` response contains `fallback_values`. Admin
`POST /domains/` and `PUT /domains/{domain_id}` accept an optional object of
recognized keys with text values of at most 256 characters. A missing key is
empty; a field set to whitespace is discarded; `{}` clears the saved object.
Omitting `fallback_values` on PUT preserves existing settings, including
requests from older clients. Unknown fields, especially `email`, are rejected
with HTTP 422. Unrelated signature and authentication APIs are unchanged.

Full application backups include the JSON database column. Domain component
exports/imports also preserve fallback fields; older archives that lack them
are interpreted as empty, without changing the existing export version.

The original `0018_large_image_uploads` schema remains intact; migration
`0019_domain_fallback_values` adds the new column and is reversible.
