# OpenSigWeave adapter architecture

**Status:** Shared adapter contract; Rspamd integration plus experimental Postfix Milter and Stalwart MTA-hook adapters. Automated adapter coverage exists, but no adapter is production-certified.  
**Scope:** Mail-server integration boundary; this document does not claim that planned adapters are implemented.

## Design rule

Initial mail-client compatibility targets and their automated format-level fixtures are tracked in [CLIENT_COMPATIBILITY.md](CLIENT_COMPATIBILITY.md). Client compatibility is separate from transport-adapter support.

OpenSigWeave core owns signature catalog, identity resolution, policy/rules, group selection, sanitization and HTML rendering. Adapters own mail-server-specific event handling, authenticated sender attribution, MIME parsing/mutation, duplicate prevention, retry/failure policy and placement before DKIM signing.

No adapter may duplicate signature-selection or template-rendering logic. No adapter may bypass sender authorization performed by the mail platform.

## Engine service extraction

The framework-independent service is implemented and documented in [ENGINE_SERVICE.md](ENGINE_SERVICE.md). HTTP/Rspamd and in-process adapters share the same typed request/result contract and selection/rendering implementation. External adapters use the versioned HTTP endpoint; in-process adapters call the shared service directly. Direct service, route and adapter regression tests are part of the CI verification.

## Versioned engine contract

Adapters running as separate processes or on separate hosts MUST call:

`GET /api/v1/engine/signatures/{url-encoded-envelope-sender}`

Required header:

- `X-Engine-Key: <engine API key>`

Optional selection headers:

- `X-OpenSigWeave-Signature: <signature name>`
- `X-OpenSigWeave-Signature-Code: <four-character selector checksum>`

The endpoint returns `Cache-Control: no-store, private` and JSON:

```json
{
  "html": "<p>Rendered signature</p>",
  "inject_on_replies": false,
  "trim_whitespace": true,
  "strip_device_signatures": false
}
```

For CID delivery, request `X-OpenSigWeave-Inline-Images: cid`. The JSON response adds
`inline_assets`: locally stored, content-addressed images with their validated
base64 bytes, MIME type and `Content-ID`. The adapter validates the fields
again, checks duplicates, wraps the modified HTML MIME body as
`multipart/related`, and never downloads image URLs during message processing.
A plain-text-only message receives no orphan image attachments.

An empty `html` means do not inject a signature. HTTP 401 means adapter credentials are invalid; 400 means malformed sender input; 422 means a stored template cannot be rendered; 503 means required signature assets are unavailable. Adapters must not interpret API errors as an empty signature: they must apply their explicitly configured fail-open/fail-closed policy and emit an operational log/metric.

The legacy `GET /api/signature/{email}` endpoint remains an alias for existing Rspamd installations. External adapters MUST use the versioned path. An adapter running inside the OpenSigWeave application may call the same engine implementation in-process, but it must preserve the same sender normalization, selection semantics, response fields and error behavior; it must not implement a second selection or rendering path.

## Adapter lifecycle

Every adapter implementation must document and test:

1. **Identity:** obtain the SMTP envelope sender from the trusted mail transaction, not an untrusted message header.
2. **Authorization:** verify that the authenticated principal is allowed to send as that envelope sender, including aliases and delegated send-as.
3. **Selection:** pass the envelope sender and optional validated concrete-signature selector to the engine API.
4. **MIME safety:** modify only supported MIME structures using a parser/library. Never reconstruct multipart messages with regular expressions or raw boundary concatenation.
5. **Idempotency:** prevent duplicate processing using trusted adapter/queue state. Never treat client-supplied `X-OpenSigWeave-Injected` headers or `<!-- OpenSigWeave:INJECTED -->` body comments as proof that an insertion already occurred; both are forgeable by the sender.
6. **Replies:** honor `inject_on_replies` and preserve quoted history.
7. **Ordering:** mutate the message before DKIM signing; verify the final delivered DKIM signature in an end-to-end test.
8. **Failure handling:** bounded network timeout/response size, TLS certificate verification, explicit fail-open/fail-closed behavior and observable errors.
9. **Security:** keep credentials outside source control, use least-privilege credentials, prevent SSRF/configurable arbitrary URL abuse, and do not trust client-supplied sender identity.
10. **Compatibility:** publish supported mail-server versions, installation/uninstall instructions and upgrade tests.

## Adapter roadmap

| Adapter | Integration mechanism | Status |
|---|---|---|
| Rspamd | Lua prefilter / HTTP engine call | Parser-backed multipart text rewrite implemented on dev; nested plain/HTML MIME and attachment fixture CI; live Mailcow SMTP/DKIM acceptance still required |
| Postfix | PyMilter sidecar using the versioned engine HTTP API | Experimental; isolated Postfix E2E verifies authenticated injection, unauthenticated no-op, control-header removal and final local delivery using a mock engine. Live engine HTTPS/API and DKIM-chain tests remain open |
| Stalwart | Native SMTP/MTA hook at DATA | Experimental adapter implemented; MIME and live SMTP validation still required |
| Exim | ACL/system-filter integration or external content filter | Planned |
| Haraka | Plugin | Planned |
| OpenSMTPD | Filter API | Planned; validate mutation support |
| Microsoft Exchange | Transport Agent / supported transport extensibility | Research required; deployment/runtime compatibility varies |
| Proxmox Mail Gateway | Postfix/Rspamd integration layer | Evaluate as a deployment recipe, not automatically a distinct engine adapter |
| Generic SMTP gateway | Explicitly configured SMTP proxy/filter | Future; requires careful queue, retry and DKIM design |

A product is not considered supported merely because it can make HTTP requests. It must expose a trusted sender identity and a safe message-mutation point before DKIM signing.

## Credential evolution

The initial contract uses the existing `ENGINE_API_KEY` for backwards compatibility. Before production or multi-tenant use, replace the shared key with independently revocable adapter credentials (adapter ID, secret hash, enabled state, scopes, last-used timestamp and rotation/revocation workflow). Do not put adapter secrets in tracked Lua/config examples.

## Built-in authenticated connection status

Admin → System displays the selected mail server and the last successful authenticated
adapter request to OpenSigWeave. The existing engine API (X-Engine-Key) and the
Stalwart DATA hook (Bearer token) are the only sources of this activity timestamp.
An unauthorized call, a failed engine request, saving the settings, or a successful
Rspamd /symbols probe **never** completes initial verification.

The timestamp is persisted in system_setup.last_authenticated_at, aggregated
to at most about one write per 45 seconds across workers, and pushed to
authenticated administrator WebSocket subscribers. The browser only updates
relative labels locally: at most two minutes = Active; then Active Xm;
from one to eight hours = Active Xh [Ym]; after eight hours = Unverified.
There is **no** basic inactivity error; a confirmed missing Rspamd module is the
only current optional advanced Error. Controller unavailable/unsupported
remains unknown, not a negative inference about SMTP.

A successful engine lookup does **not** prove MIME rewriting, SMTP acceptance,
final DKIM verification or current Rspamd health. A configured mail adapter
that sends no mail may appear Unverified. This mechanism uses the existing
shared adapter credential; deployments needing cryptographically distinct
mailserver identities should migrate to per-adapter revocable tokens when that
feature becomes available.

The same setup works when OpenSigWeave and a mailserver share a host, use
different Docker networks, or run on separate hosts: configure a reachable
HTTPS engine URL and trusted TLS certificate and the matching existing API
key. No additional Compose service, published Rspamd controller port, Docker
socket or monitoring plugin is required. OpenSigWeave's Admin → System screen
offers the minimal Rspamd files and a copyable configuration template.
Pin the Rspamd files to the same *tested* commit as the engine before enabling
production mail. Reconfiguring the mail adapter or engine URL requires a fresh
authenticated request; enabling advanced monitoring does not reset verification.

## Release gates

An adapter is releasable only after unit/contract tests, static analysis, supported-version documentation and an isolated SMTP end-to-end test covering plain text, HTML, multipart/alternative, attachments, inline Content-IDs, transfer encodings, replies, retries, duplicate prevention, sender spoofing rejection and final DKIM validation. Unsupported MIME cases must be skipped safely, not corrupted.

## Postfix Milter adapter (experimental)

`adapters/postfix_milter.py` implements the SMTP Milter sidecar using PyMilter 1.0.6. It obtains the envelope sender from the Postfix transaction, requires a non-empty SASL identity, passes the optional mode/signature selectors to the versioned engine endpoint over HTTPS, and leaves sender/alias authorization to Postfix (`smtpd_sender_login_maps` plus `reject_authenticated_sender_login_mismatch`). It performs bounded MIME rewriting, preserves binary attachment payloads in tests, skips signed/encrypted messages, honors reply placement, removes client-supplied OpenSigWeave control headers and adds a processing marker after successful injection. Neither the processing header nor an HTML body comment is trusted as an idempotency signal on input, because either can be forged by the SMTP sender. The isolated CI integration starts the real adapter callback class in PyMilter, connects a disposable Postfix SMTP server, and verifies final delivered MIME for authenticated and unauthenticated messages. The engine is stubbed in that test: real API credentials/TLS trust, DKIM ordering and a production Postfix deployment are not yet certified. See [POSTFIX_MILTER_DESIGN.md](POSTFIX_MILTER_DESIGN.md).

## Stalwart MTA-hook adapter (experimental)

OpenSigWeave exposes `POST /api/v1/adapters/stalwart/mta-hook`. Configure the Stalwart hook for the `data` stage only and use Bearer authentication with the `STALWART_HOOK_TOKEN` secret. The endpoint requires an authenticated SASL session; unauthenticated inbound mail is accepted unchanged. Stalwart must independently enforce envelope-sender authorization, including aliases and delegated send-as permissions.

The adapter obtains the sender from the SMTP envelope, invokes the shared framework-independent engine service in-process (rather than making a loopback HTTP request or calling the FastAPI handler), and translates the rendered signature into Stalwart `replaceContents` / header modifications. It defers rotation commits until the final MIME rewrite is accepted; if the message is signed, malformed or otherwise unchanged, pending rotation state is rolled back. It does not use the HTTP engine credential; its Bearer token authenticates the hook request. This is an implementation optimization, not a separate engine contract; the handler and the external versioned HTTP endpoint share the same implementation. It supports text/plain, text/html and multipart MIME trees using Python's email parser. Attachment and inline-image parts are not modified and are preserved when the message is serialized; signed or encrypted structures and malformed outer MIME trees are left unchanged. The hook request body is limited to 20 MiB. It defers removal of OpenSigWeave control headers (including the processing marker) until a body rewrite is actually emitted, and adds a processing header after successful insertion. Client-supplied processing headers and HTML body comments are never trusted to suppress signature injection. Messages with no selected signature or any other no-op condition return an empty accept response rather than header-only modifications; this avoids superseding body changes made by an earlier Stalwart Milter.

**Important limitations:** this is an experimental first implementation, not yet a production-certified mail adapter. It does not yet implement the Rspamd adapter's body-marker selection, device-signature cleanup or whitespace trimming. It has no live Stalwart SMTP/DKIM end-to-end test yet. Configure Stalwart's `tempFailOnError` deliberately and keep its HTTP timeout bounded. Stalwart executes MTA hooks after milters. Its hook contract states that a non-empty list of modifications returned by a hook replaces body changes requested by milters. Consequently, an OpenSigWeave response containing only header deletions (for example, control-header cleanup when no signature is selected) can still supersede a body rewrite performed by an earlier Milter such as Rspamd. This is not limited to a later body-mutating hook. Each hook is evaluated against the original message state, so hook ordering must not be treated as a way to compose independent body edits. For production deployments, make OpenSigWeave the only body-mutating stage in the Stalwart filter chain, or validate the exact Stalwart version and complete Milter/hook combination with end-to-end SMTP tests. The current adapter has no live test proving safe composition with prior Milter body mutations.
