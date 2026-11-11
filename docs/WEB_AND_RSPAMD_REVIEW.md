# Web UI and integration review

**Reviewed:** `main.py`, `templates/index.html`, `templates/error.html`, `rspamd/opensigweave.lua`  
**Baseline:** current `main` content as inspected on 2026-11-10

## Web UI observations

1. The admin interface calls the unauthenticated domain CRUD routes directly (`/domains/` and `/domains/{id}`). This matches finding SEC-02 in the repository audit: hiding the admin tab in the template is not an authorization boundary.
2. The preview renders signature HTML using Alpine `x-html`. This is an intentional HTML preview feature, but it means untrusted HTML can execute in the application origin unless a strict sanitization policy is enforced before preview. The current HTML import path removes literal script elements with a regular expression; that is not a complete HTML sanitizer and does not cover event-handler attributes, dangerous URL schemes, SVG or malformed markup.
3. The template interpolates profile values into a JavaScript object literal. Those values need context-appropriate JavaScript serialization/escaping rather than relying on ordinary HTML template escaping.
4. The profile settings link is opened in a new tab. Add `rel="noopener noreferrer"` when retaining `target="_blank"`.
5. Quill and Alpine are loaded from public CDNs without Subresource Integrity attributes or pinned immutable asset URLs. This creates a third-party supply-chain dependency in the administrative UI. Prefer self-hosted, version-pinned assets or integrity-pinned CDN resources.
6. State-changing actions use cookie-authenticated `fetch` calls and no CSRF token is visible in the inspected template. Add CSRF tokens and server-side validation; SameSite cookies and Origin checks are defense-in-depth, not substitutes for a complete threat model.
7. Logout is implemented as a GET route. Prefer POST with CSRF protection for a state-changing session action.
8. The access-denied template uses normal Jinja interpolation for the message; retain autoescaping and do not change this to `safe`.

## Rspamd integration review

- The module obtains the sender from `task:get_from(1)`, while also requiring `task:get_user()` to be present. The trust relationship between envelope sender, authenticated SMTP identity and header From must be documented and tested.
- The sender is concatenated into the API path. Use a URL-safe encoding strategy or a request body in a versioned API contract; validate normalized email input on the server.
- The HTTP request has no explicit timeout, TLS policy, retry policy or documented failure mode in the visible request options.
- The response body is parsed and used to reconstruct MIME content. Validate response shape and size before using it.
- The callback mutates the message through `task:set_message`. Ensure it is invoked only once per message and add an idempotency marker or equivalent guard.
- The module's manual MIME boundary search and transfer-encoding reconstruction need fixture-driven tests. The current implementation does not demonstrate correct handling of nested multipart structures or arbitrary MIME trees.
- A `prefilter` registration is not sufficient evidence that message mutation precedes DKIM signing. Test against the effective Rspamd/Mailcow configuration and verify the delivered DKIM signature cryptographically.

## Suggested first test matrix

| Area | Cases |
|---|---|
| Authorization | Anonymous, normal user, admin; each domain CRUD method |
| CSRF | Missing token, invalid token, valid token; cross-origin request |
| HTML | Script tag, event handler, javascript URL, SVG payload, valid table-based signature |
| OIDC | Valid login, invalid state, expired session, missing email, missing group |
| Engine API | Missing/wrong/rotated credential, malformed email, oversized response, timeout |
| MIME | Plain text, HTML, multipart/alternative, multipart/mixed with attachment, nested multipart, base64, quoted-printable |
| Mail behavior | New message, reply, forwarded message, duplicate processing, no matching signature |
| DKIM | Final delivered message passes DKIM after signature injection |

## Immediate implementation guardrails

- Do not solve the UI preview issue by stripping all rich email HTML; email signatures legitimately use tables and inline styles. Define a sanitizer/preview isolation strategy that preserves required email markup.
- Do not trust a browser-side role check. Enforce permissions in FastAPI dependencies for every protected route.
- Do not log tokens, engine credentials, full message bodies or signature HTML.
- Keep Rspamd-specific parsing and mutation isolated behind an adapter boundary.

## SMTP sender authorization boundary

Rspamd's `task:get_user()` only establishes that the message was submitted through an authenticated SMTP session. It does **not** prove that the authenticated mailbox is authorized to use the envelope sender returned by `task:get_from(1)`. OpenSigWeave currently uses that envelope sender to select the signature identity.

Therefore, sender ownership must be enforced by Mailcow/Postfix **before** the message reaches the signature hook:

- Keep Mailcow's sender verification enabled. Do not disable sender checks globally and do not grant a mailbox the broad `Allow to send as *` permission merely to make signature selection work.
- Use Mailcow's mailbox alias and delegated-send-as controls to grant only the addresses that the authenticated mailbox may legitimately use. Mailcow's sender/receiver model documents that mailbox aliases and alias domains can be authorized send-as identities, while catch-all aliases do not automatically grant send-as permission.
- Preserve Postfix's authenticated-sender/login mismatch rejection. Do not add broad `OK` exceptions to `smtpd_sender_restrictions` without a narrowly scoped, reviewed requirement.
- Treat the SMTP envelope sender as the signature identity. Do not switch to the untrusted message `From:` header for authorization or identity selection.

### Required Mailcow integration tests

Run these tests in a disposable Mailcow environment before enabling the integration:

| Authenticated SMTP identity | Envelope sender | Expected result |
|---|---|---|
| Mailbox address | Same mailbox address | Accepted; matching signature identity |
| Mailbox address | Explicitly configured alias | Accepted; alias signature policy applied |
| Mailbox address | Explicitly delegated send-as address | Accepted only when delegation is configured |
| Mailbox address | Unrelated mailbox address | Rejected by Postfix before signature injection |
| Mailbox address | Unauthorized address in same domain | Rejected by Postfix before signature injection |
| Unauthenticated SMTP client | Any local sender | Rejected or relay denied according to the public SMTP policy |

Also verify that the effective Postfix configuration still contains the authenticated sender/login mismatch enforcement after Mailcow updates and custom configuration reloads. Mailcow's current documentation exposes sender verification and per-mailbox send-as permissions through its UI; use those supported controls rather than copying deprecated global Postfix overrides.

**Status:** This is a documented integration prerequisite, not a verified production setting. No Mailcow configuration has been changed and no end-to-end SMTP test has been run.
