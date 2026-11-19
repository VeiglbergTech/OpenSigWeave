# Postfix Milter adapter — technical design

**Status:** The experimental Postfix Milter adapter is implemented on `dev`. PyMilter 1.0.6 runtime/API, real Postfix SMTP/Milter protocol exchange, authenticated delivery through the actual adapter, control-header removal and final local-delivered MIME are CI-validated. The adapter E2E uses a mock engine. Live engine HTTPS/API integration, DKIM-chain validation, production image packaging, concurrency/load behavior and production deployment validation remain open. PyMilter licensing and long-term packaging suitability still require review.  
**Target branch:** `dev`  
**Scope:** Track the implementation boundary, evidence and remaining release gates for the experimental Postfix adapter.

## Decision summary

Postfix is a strong next adapter candidate. Its SMTP server supports the Sendmail version 8 Milter protocol before queueing, and the protocol supports inspection of SMTP events, envelope data, headers and message body. Postfix can pass a message through multiple Milters in configured order. The adapter must therefore be ordered before the DKIM signer and coordinated with every other body-mutating filter.

The first implementation should be a small, independently deployable Milter sidecar. It must call the existing versioned OpenSigWeave engine endpoint; it must not import the application database or duplicate selection, rules, identity resolution or rendering.

**Selected experimental runtime: PyMilter 1.0.6. It is CI-validated but not approved for production.** PyMilter wraps Sendmail's native `libmilter` API and provides a Python object-oriented callback interface. The PyPI project page still exposes 1.0.5 (uploaded in 2021), while current distribution packaging has moved to 1.0.6 and includes Python 3.12 builds. This is encouraging evidence, but it is not proof that the exact Python 3.12-slim container used by OpenSigWeave can build, import and run the library correctly. The 1.0.6 distribution packages also require the native `libmilter` shared library. PyMilter is GPL-2.0-or-later, which must be reviewed for the separately distributed sidecar.

The isolated CI workflow installs the EPEL 10 package `python3-pymilter-1.0.6-14.el10_2` on Python 3.12. It verifies imports of `Milter` and the native `milter` extension, checks that the extension resolves `libmilter.so.1.0`, and exercises the envelope sender, SASL macro lookup, header, body and end-of-message body-replacement API against a fake libmilter context. The runtime/API checks passed in Actions run #44135395448. The same run also started a disposable Postfix instance, connected PyMilter over TCP, submitted an unauthenticated SMTP message and verified real `envfrom`, `header`, `body` and `eom` callbacks plus `addheader`. The Milter requests `{auth_authen}` via `@Milter.symlist`; the real unauthenticated transaction correctly exposes no authenticated user. A new CI extension now provisions a disposable Cyrus SASL `sasldb` account, authenticates an SMTP session, checks that the real `{auth_authen}` macro reaches the Milter, accepts an authorized envelope sender and rejects an unauthorized sender via `smtpd_sender_login_maps` and `reject_authenticated_sender_login_mismatch`. The authenticated scenario passed in Actions run #44165781769 on commit `ed9d15e30dfcf0f688ff1357c975027cd38ea9de`. This validates real SMTP AUTH identity propagation and Postfix sender-ownership enforcement. A subsequent protocol test in Actions run #44167118606 on commit `3513f89fde51d0aa1a5ae32f1ff20c79a02d2473` also invoked `addheader`, `chgheader` and `replacebody` from the real Postfix `eom` callback with the required mutation flags. The protocol probe verifies callback execution; a separate `postfix-opensigweave-adapter` CI job now runs the actual OpenSigWeave Milter class against disposable Postfix and inspects the final delivered MIME. The engine response is stubbed, so this proves the adapter/Milter/Postfix mutation path but not live engine HTTP integration or DKIM behavior. The HTTP client validates the complete typed response contract (`html` plus three boolean policy fields) and rejects missing or mistyped policy values instead of coercing strings such as `false` to truthy values.

The Go `andybalholm/milter` package remains an alternative only if the Python/native dependency route proves unsuitable. A custom protocol implementation is not the default fallback: the Milter wire protocol, negotiation flags, chunk framing and failure semantics are security-sensitive. Prefer a maintained implementation with tests over maintaining a protocol stack ourselves.

## Proposed transaction flow

1. Postfix invokes the adapter through `smtpd_milters` for SMTP-submitted messages.
2. The Milter receives the SMTP envelope sender from the envelope callback. It must not derive the sender from the RFC 5322 `From:` header.
3. The adapter receives the complete message headers/body and assembles a bounded message representation.
4. At end-of-message, it checks whether the message is eligible:
   - authenticated submission only by default;
   - envelope sender is non-empty and normalized;
   - sender-supplied processing markers are ignored and removed; any duplicate suppression must rely on trusted MTA/queue state;
   - signed/encrypted or unsupported MIME structures are left unchanged;
   - configured maximum message size and timeout are respected.
5. It calls `GET /api/v1/engine/signatures/{url-encoded-envelope-sender}` with `X-Engine-Key` and optional validated selection headers.
6. It parses and mutates MIME using a standards-aware email library. It must preserve attachments, inline Content-IDs, transfer encodings and unrelated MIME parts. It must never rebuild MIME using regular expressions or raw boundary concatenation.
7. It emits body/header changes only if a real insertion occurred. Empty engine output, ineligible messages and safe no-op cases are accepted unchanged.
8. It adds an informational processing marker only after successful insertion. The marker is never trusted on input and cannot provide idempotency across independent message submissions or reprocessing. Any such duplicate suppression requires trusted MTA/queue state; the current adapter does not implement that state.
9. It returns the configured Milter action on engine or parsing failure. Default deployment guidance should favor temporary failure until operators deliberately choose fail-open behavior.

## Identity and authorization

The SMTP envelope sender is the authoritative address for signature lookup, but it is not by itself proof that the authenticated principal may send as that address.

Before enabling injection, configure Postfix to expose the authenticated SASL identity to the Milter using `milter_mail_macros` and request `{auth_authen}` at the MAIL FROM stage with PyMilter's `@Milter.symlist('{auth_authen}')`. The adapter must require a non-empty authenticated identity. Enforce envelope-sender ownership in Postfix with `smtpd_sender_login_maps` and `reject_authenticated_sender_login_mismatch`; the map must include approved aliases and delegated send-as owners. This keeps SMTP authorization in the MTA's policy layer instead of duplicating it in the adapter. If the SASL identity is absent, accept unchanged. Do not infer authorization from the message's `From:` header.

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
- Build and import PyMilter 1.0.6 in a Python 3.12 container with the required `libmilter` development/runtime packages; record the exact source and package versions.
- Verify real protocol negotiation and envelope sender, SASL macro visibility, header, body-chunk and end-of-message callbacks against a disposable Postfix instance. Verify `smtpd_sender_login_maps` rejects an authenticated but unauthorized envelope sender. Invoke header/body mutation APIs under negotiated flags and inspect the final locally delivered message. These checks currently pass in the isolated Postfix compatibility workflow.
- Test clean startup/shutdown, socket permissions, concurrency and bounded memory under large messages.
- Review GPL-2.0-or-later distribution implications and dependency maintenance before adoption.

### Adapter tests
- New plain-text message; HTML message; multipart/alternative.
- Attachments and inline Content-ID images preserved byte-semantically where feasible.
- Quoted replies follow `inject_on_replies`; a sender-supplied processing marker must not suppress insertion. Duplicate suppression across independent reprocessing is not implemented until trusted MTA/queue state is integrated.
- Empty envelope sender, unauthenticated sender, spoofed `From:`, unauthorized alias and missing SASL macro all produce no injection.
- Signed/encrypted messages and malformed MIME remain unchanged.
- Engine 401/400/422/503, timeout, oversized response, malformed JSON and incomplete/mistyped typed policy fields exercise explicit failure policy.
- Confirm no Milter mutation is emitted when insertion is skipped or fails.

### End-to-end release gate
Run a disposable Postfix instance with the adapter, a live OpenSigWeave engine and a DKIM signer in the intended order. Send messages through SMTP AUTH and verify delivered MIME, sender authorization, retry behavior, rejection of forged processing markers and final DKIM validation. The current CI uses a mock engine and does not satisfy this release gate. Do not claim exactly-once processing across independent submissions until trusted MTA/queue state is integrated and tested. Unit tests or a protocol simulator alone do not qualify as Postfix integration validation.

## References

- Postfix Milter integration and limitations: https://www.postfix.org/MILTER_README.html
- Postfix Milter configuration parameters: https://www.postfix.org/postconf.5.html
- PyMilter project and API documentation: https://www.pymilter.org/
- PyMilter source: https://github.com/sdgathman/pymilter
- PyMilter PyPI release history: https://pypi.org/project/pymilter/
- Python 3.12 distribution packaging evidence: https://packages.fedoraproject.org/pkgs/python-pymilter/python3-pymilter/
- Python 3.12 package dependency evidence: https://portstatus.bsdproject.de/mail/py-pymilter/
- Go Milter alternative: https://github.com/andybalholm/milter


## Postfix body cleanup policy

The Postfix adapter consumes the engine's typed `trim_whitespace` and
`strip_device_signatures` booleans. Cleanup runs only on decoded editable
`text/plain` and `text/html` MIME parts, before reply-boundary insertion;
attachments and unsupported/signed MIME structures remain untouched.
Whitespace trimming removes common trailing editor whitespace and empty HTML
blocks. Device-signature stripping matches the Rspamd adapter's supported
plain-text lines (`Sent from my …`, `Get Outlook for …`) and corresponding
HTML lines following a `<br>`. False policy values preserve that content.
Unit tests cover both policies; Postfix E2E still uses a mock engine and does
not yet prove a live engine policy response.


## Untrusted processing markers

`X-OpenSigWeave-Injected` and the historical `<!-- OpenSigWeave:INJECTED -->` HTML comment are sender-controlled when received in an SMTP message. They must never be treated as proof that an adapter already processed the message. The adapter removes client-supplied control headers and performs its normal eligibility/rewrite checks. The marker added after insertion is informational only. The current adapter does not implement trusted MTA/queue state, so it makes no exactly-once guarantee across independent submissions or reprocessing; any future duplicate suppression must use trusted state, never message content supplied by the sender.
