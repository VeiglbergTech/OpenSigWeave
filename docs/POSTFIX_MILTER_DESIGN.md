# Postfix Milter adapter — technical design

**Status:** Research complete; implementation not started  
**Target branch:** `dev`  
**Scope:** Design the Postfix integration boundary without adding an unverified Milter runtime dependency.

## Decision summary

Postfix is a strong next adapter candidate. Its SMTP server supports the Sendmail version 8 Milter protocol before queueing, and the protocol supports inspection of SMTP events, envelope data, headers and message body. Postfix can pass a message through multiple Milters in configured order. The adapter must therefore be ordered before the DKIM signer and coordinated with every other body-mutating filter.

The first implementation should be a small, independently deployable Milter sidecar. It must call the existing versioned OpenSigWeave engine endpoint; it must not import the application database or duplicate selection, rules, identity resolution or rendering.

**No Milter library is selected yet.** A short compatibility spike is required before adding a dependency:
- `pymilter` is attractive because the application is Python-based, but it wraps the native Sendmail `libmilter` API. The upstream project advertises version 1.0.5, while its own compatibility notes still mention Python 2 testing. Python 3.12 build/runtime compatibility and the required system library must be proven in CI before adoption.
- The Go `andybalholm/milter` package exposes a small Milter interface and BSD-2-Clause license, but the repository currently shows only two commits. It is not sufficiently established to select solely from its API shape.
- A custom protocol implementation is not the default fallback: the Milter wire protocol, negotiation flags, chunk framing and failure semantics are security-sensitive. Prefer a maintained implementation with tests over maintaining a protocol stack ourselves.

## Proposed transaction flow

1. Postfix invokes the adapter through `smtpd_milters` for SMTP-submitted messages.
2. The Milter receives the SMTP envelope sender from the envelope callback. It must not derive the sender from the RFC 5322 `From:` header.
3. The adapter receives the complete message headers/body and assembles a bounded message representation.
4. At end-of-message, it checks whether the message is eligible:
   - authenticated submission only by default;
   - envelope sender is non-empty and normalized;
   - message is not already marked as processed;
   - signed/encrypted or unsupported MIME structures are left unchanged;
   - configured maximum message size and timeout are respected.
5. It calls `GET /api/v1/engine/signatures/{url-encoded-envelope-sender}` with `X-Engine-Key` and optional validated selection headers.
6. It parses and mutates MIME using a standards-aware email library. It must preserve attachments, inline Content-IDs, transfer encodings and unrelated MIME parts. It must never rebuild MIME using regular expressions or raw boundary concatenation.
7. It emits body/header changes only if a real insertion occurred. Empty engine output, ineligible messages and safe no-op cases are accepted unchanged.
8. It adds an idempotency marker only after successful insertion. Retries must not duplicate the signature.
9. It returns the configured Milter action on engine or parsing failure. Default deployment guidance should favor temporary failure until operators deliberately choose fail-open behavior.

## Identity and authorization

The SMTP envelope sender is the authoritative address for signature lookup, but it is not by itself proof that the authenticated principal may send as that address.

Before enabling injection, configure Postfix to expose the authenticated SASL identity to the Milter using the appropriate Milter macro configuration for the supported Postfix version. The adapter must compare that authenticated identity with an explicit authorization policy for the envelope sender, including aliases and delegated send-as. If the SASL identity is absent, ambiguous or unauthorized, accept unchanged. Do not infer authorization from the message's `From:` header.

Inbound unauthenticated mail must remain unchanged. Local/non-SMTP submission is out of scope for the first version because Postfix uses a separate `non_smtpd_milters` path with different capabilities and identity context.

## Postfix integration constraints

- Use `smtpd_milters` for the first release; do not enable `non_smtpd_milters` by default.
- Keep the Milter socket private (Unix socket or loopback only); do not expose an unauthenticated Milter listener to untrusted networks.
- Configure explicit connect, command and content timeouts.
- Configure `milter_default_action` deliberately. A temporary failure is safer than silently delivering unsigned-by-policy mail during an engine outage; fail-open can be an operator override.
- Verify the supported Milter protocol version and negotiated capabilities against the selected library and supported Postfix versions.
- Order the signature adapter before DKIM signing. Any later body-mutating Milter can invalidate the DKIM signature.
- Document that Postfix has separate SMTP and non-SMTP Milter chains; the initial adapter only covers SMTP submission.
- Use a dedicated least-privilege service identity and external secret injection for the engine key.

## Required test plan

### Library/runtime spike
- Build and import the selected Milter library in the same Python 3.12 / container environment used by the project, or document a separately pinned sidecar runtime.
- Verify protocol negotiation, envelope sender callback, SASL macro visibility, header callbacks, body chunk callbacks, end-of-message callback and body replacement.
- Test clean startup/shutdown, socket permissions, concurrency and bounded memory under large messages.
- Verify licensing and dependency maintenance before adoption.

### Adapter tests
- New plain-text message; HTML message; multipart/alternative.
- Attachments and inline Content-ID images preserved byte-semantically where feasible.
- Quoted replies follow `inject_on_replies`; no duplicate insertion on retries.
- Empty envelope sender, unauthenticated sender, spoofed `From:`, unauthorized alias and missing SASL macro all produce no injection.
- Signed/encrypted messages and malformed MIME remain unchanged.
- Engine 401/400/422/503, timeout, oversized response and malformed response exercise explicit failure policy.
- Confirm no Milter mutation is emitted when insertion is skipped or fails.

### End-to-end release gate
Run a disposable Postfix instance with the adapter and a DKIM signer in the intended order. Send messages through SMTP AUTH and verify delivered MIME, sender authorization, retry behavior, idempotency and final DKIM validation. Unit tests or a protocol simulator alone do not qualify as Postfix integration validation.

## References

- Postfix Milter integration and limitations: https://www.postfix.org/MILTER_README.html
- Postfix Milter configuration parameters: https://www.postfix.org/postconf.5.html
- PyMilter project and compatibility notes: https://www.pymilter.org/
- PyMilter source: https://github.com/sdgathman/pymilter
- Go Milter candidate: https://github.com/andybalholm/milter
