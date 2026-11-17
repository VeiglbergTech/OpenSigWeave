# OpenSigWeave adapter architecture

**Status:** Adapter contract plus experimental Stalwart MTA-hook adapter, development preview  
**Scope:** Mail-server integration boundary; this document does not claim that planned adapters are implemented.

## Design rule

OpenSigWeave core owns signature catalog, identity resolution, policy/rules, variant and group selection, sanitization and HTML rendering. Adapters own mail-server-specific event handling, authenticated sender attribution, MIME parsing/mutation, duplicate prevention, retry/failure policy and placement before DKIM signing.

No adapter may duplicate signature-selection or template-rendering logic. No adapter may bypass sender authorization performed by the mail platform.

## Engine service extraction

The current in-process implementation and the planned framework-independent service boundary are documented in [ENGINE_SERVICE.md](ENGINE_SERVICE.md). The service extraction is not yet implemented; adapters must continue to follow the current shared-handler behavior until the documented parity tests pass.

## Versioned engine contract

Adapters running as separate processes or on separate hosts MUST call:

`GET /api/v1/engine/signatures/{url-encoded-envelope-sender}`

Required header:

- `X-Engine-Key: <engine API key>`

Optional selection headers:

- `X-OpenSigWeave-Mode: AUTO | PROFESSIONAL | INTERNAL | NONE`
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

An empty `html` means do not inject a signature. HTTP 401 means adapter credentials are invalid; 400 means malformed sender input; 422 means a stored template cannot be rendered; 503 means required signature assets are unavailable. Adapters must not interpret API errors as an empty signature: they must apply their explicitly configured fail-open/fail-closed policy and emit an operational log/metric.

The legacy `GET /api/signature/{email}` endpoint remains an alias for existing Rspamd installations. External adapters MUST use the versioned path. An adapter running inside the OpenSigWeave application may call the same engine implementation in-process, but it must preserve the same sender normalization, selection semantics, response fields and error behavior; it must not implement a second selection or rendering path.

## Adapter lifecycle

Every adapter implementation must document and test:

1. **Identity:** obtain the SMTP envelope sender from the trusted mail transaction, not an untrusted message header.
2. **Authorization:** verify that the authenticated principal is allowed to send as that envelope sender, including aliases and delegated send-as.
3. **Selection:** pass the envelope sender and optional validated mode/selector to the engine API.
4. **MIME safety:** modify only supported MIME structures using a parser/library. Never reconstruct multipart messages with regular expressions or raw boundary concatenation.
5. **Idempotency:** detect an existing OpenSigWeave insertion and never append duplicates on retries or reinjection.
6. **Replies:** honor `inject_on_replies` and preserve quoted history.
7. **Ordering:** mutate the message before DKIM signing; verify the final delivered DKIM signature in an end-to-end test.
8. **Failure handling:** bounded network timeout/response size, TLS certificate verification, explicit fail-open/fail-closed behavior and observable errors.
9. **Security:** keep credentials outside source control, use least-privilege credentials, prevent SSRF/configurable arbitrary URL abuse, and do not trust client-supplied sender identity.
10. **Compatibility:** publish supported mail-server versions, installation/uninstall instructions and upgrade tests.

## Adapter roadmap

| Adapter | Integration mechanism | Status |
|---|---|---|
| Rspamd | Lua prefilter / HTTP engine call | Existing; non-multipart rewrite only |
| Postfix | Milter or dedicated content-filter service | Planned |
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

## Release gates

An adapter is releasable only after unit/contract tests, static analysis, supported-version documentation and an isolated SMTP end-to-end test covering plain text, HTML, multipart/alternative, attachments, inline Content-IDs, transfer encodings, replies, retries, duplicate prevention, sender spoofing rejection and final DKIM validation. Unsupported MIME cases must be skipped safely, not corrupted.

## Stalwart MTA-hook adapter (experimental)

OpenSigWeave exposes `POST /api/v1/adapters/stalwart/mta-hook`. Configure the Stalwart hook for the `data` stage only and use Bearer authentication with the `STALWART_HOOK_TOKEN` secret. The endpoint requires an authenticated SASL session; unauthenticated inbound mail is accepted unchanged. Stalwart must independently enforce envelope-sender authorization, including aliases and delegated send-as permissions.

The adapter obtains the sender from the SMTP envelope, invokes the shared engine handler in-process (rather than making a loopback HTTP request), and translates the rendered signature into Stalwart `replaceContents` / header modifications. This is an implementation optimization, not a separate engine contract; the handler and the external versioned HTTP endpoint share the same implementation. It supports text/plain, text/html and multipart MIME trees using Python's email parser. Attachment and inline-image parts are not modified and are preserved when the message is serialized; signed or encrypted structures and malformed outer MIME trees are left unchanged. The hook request body is limited to 20 MiB. It removes the OpenSigWeave control headers and adds an idempotency header after successful insertion.

**Important limitations:** this is an experimental first implementation, not yet a production-certified mail adapter. It does not yet implement the Rspamd adapter's body-marker selection, device-signature cleanup or whitespace trimming. It has no live Stalwart SMTP/DKIM end-to-end test yet. Configure Stalwart's `tempFailOnError` deliberately and keep its HTTP timeout bounded. Do not run another body-mutating MTA hook after this adapter: Stalwart documents that non-empty hook modifications replace earlier milter body modifications. The adapter should therefore be ordered and tested with the complete filter chain.
