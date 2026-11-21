# Mailcow / Rspamd adapter integration (staging only)

**Status:** MIME rewrite regression tests run on `dev` with Lua fixtures parsed again by Python's real MIME parser (including nested sibling multipart, Unicode, long headers, inline CIDs and binary attachments). These are *simulation/serializer tests*, not a real Rspamd/Mailcow SMTP submission. No live Mailcow submission/DKIM acceptance has passed. Do **not** enable this on a production mail flow until the acceptance checks below succeed on a disposable Mailcow instance.

OpenSigWeave runs in its own Compose stack. The adapter runs inside Mailcow's Rspamd. Do not merge or replace Mailcow's bundled `rspamd.local.lua`.

## Install in a disposable Mailcow instance

Run from the Mailcow checkout directory; substitute `/path/to/OpenSigWeave` for the checkout of the *same tested OpenSigWeave commit*.

```bash
cd /opt/mailcow-dockerized
install -d -m 0755 data/conf/rspamd/plugins.d data/conf/rspamd/lua
install -m 0644 /path/to/OpenSigWeave/rspamd/opensigweave.lua data/conf/rspamd/plugins.d/opensigweave.lua
install -m 0644 /path/to/OpenSigWeave/rspamd/opensigweave_mime.lua data/conf/rspamd/lua/opensigweave_mime.lua
```

Add **one** `opensigweave` section to `data/conf/rspamd/rspamd.conf.local` (edit an existing section rather than duplicating it):

```ucl
opensigweave {
    api_url = "https://signature.example.org/api/v1/engine/signatures/";
    engine_api_key = "REPLACE_WITH_DISTINCT_RANDOM_ENGINE_KEY_AT_LEAST_32_CHARS";
    timeout = 5.0;
    max_response_size = 65536;
}
```

The key must match OpenSigWeave's `ENGINE_API_KEY`. Keep the real key out of Git. Ensure Rspamd trusts the OpenSigWeave HTTPS certificate; do not disable verification. The adapter uses the SMTP envelope sender and requires an authenticated submission, but Mailcow/Postfix **must independently authorize** that sender, including aliases and delegated send-as addresses.

Before enabling, verify the module and configuration in your disposable environment:

```bash
docker compose exec rspamd-mailcow rspamadm configtest
docker compose restart rspamd-mailcow
docker compose logs --tail=100 rspamd-mailcow
```

If Rspamd reports a missing `opensigweave_mime` module, confirm that `data/conf/rspamd/lua/opensigweave_mime.lua` exists and is readable inside the container at `/etc/rspamd/lua/opensigweave_mime.lua`. Do not proceed with a startup error.

## Supported MIME scope

For supported outgoing SMTP submissions, the adapter uses Rspamd's parsed MIME parts rather than regular expressions over encoded message bodies. It rewrites `text/plain` and `text/html` leaves of `multipart/alternative`, `multipart/related` and `multipart/mixed` messages (including nested combinations); non-text parts, binary attachments and inline image/CID parts are carried through unchanged. Text is decoded through Rspamd and re-encoded as UTF-8 quoted-printable. Standalone `text/plain` is currently converted to `multipart/alternative`; this is intentional existing behavior, and the HTML-only path remains HTML-only. Validate the converted output explicitly with the sending mail clients. Recognized reply boundaries use the existing SOGo, Outlook and Thunderbird placement logic. Messages over 20 MiB, multipart messages with over 256 MIME parts and messages with over 256 KiB of headers are skipped before the engine call to bound memory/CPU exposure.

MIME signed/encrypted parts, `message/rfc822` attachments, missing boundaries and undecodable text parts cause a **whole-message skip**, not a partial rewrite. Malformed selection-marker cleanup is deliberately skipped on multipart API failures instead of risking raw-body corruption. A green CI test proves the fixture passes, not that a specific Mailcow/Rspamd release is certified.

## Required end-to-end acceptance

1. Ensure Mailcow's own **domain-wide footer** is disabled for the test domain to avoid two independent body rewriters.
2. Verify authenticated sender ownership and alias/send-as policy with positive and negative tests. An arbitrary authenticated user must not be able to sign as another mailbox.
3. Send a new HTML message from SOGo containing `multipart/alternative` plus a binary attachment and an inline image. Inspect the delivered raw message and check *both* text alternatives, Content-ID references and attachment hashes.
4. Reply and forward from SOGo, Outlook and Thunderbird. The new signature must appear after the new text and **before** quoted history. Confirm there is no second signature upon reprocessing.
5. Send base64- and quoted-printable-encoded bodies; verify their decoded rendered content. Repeat with nested `multipart/mixed` / `multipart/related`.
6. Send test S/MIME/PGP signed and encrypted messages. They must not be modified.
7. Inspect the effective Rspamd plugin order and DKIM signing order. The **final received message must pass DKIM** and must not be changed after signing.
8. Test engine errors, timeout, TLS failure and restart. Decide explicitly whether messages must be delivered without a signature or temporarily rejected; do not silently lose or corrupt mail.

Rollback: remove `data/conf/rspamd/plugins.d/opensigweave.lua`, remove the matching config section and restart only the Rspamd service. Keep the helper file if other installations still reference it. Retest normal Mailcow delivery and DKIM.

Official references: [Mailcow custom Rspamd modules](https://docs.mailcow.email/manual-guides/Rspamd/u-e-rspamd-add-additional-modules/), [Rspamd MIME API](https://docs.rspamd.com/lua/lua_mime/) and [Rspamd DKIM signing](https://docs.rspamd.com/modules/dkim_signing/).


## Technical audit — v1.0 Mailcow beta gate (2026-11-20)

The repository validates the Rspamd adapter's **Lua logic and MIME output**, but does not run a real Mailcow Rspamd instance in CI. The separate Postfix-Milter compatibility job is a different adapter and must **not** be taken as proof of this Rspamd integration.

| Mail type / case | Automated coverage | Mandatory Mailcow beta verification |
| --- | --- | --- |
| Standalone plain text and HTML | Source contract, decoding/placement logic; **no live MIME SMTP** | Send 7bit, quoted-printable and base64, including non-UTF-8 charsets; verify decoded body and MIME headers |
| multipart/alternative | Lua fixture + Python MIME parser | Both alternatives display one corresponding signature |
| Attachments, binary/non-text leaves | Lua fixture + decoded hash/value checks | Compare SHA-256 of sent/received attachments, file names and dispositions |
| Inline CID images, multipart/related | Nested fixture + parser checks | CID references render, no missing inline resources |
| Multiple nested multipart siblings | New Lua/Python regression matrix | Forward multipart mail and inspect structure/attachments |
| Replies and forwards | Reply-boundary regression fixtures | Validate SOGo, Outlook and Thunderbird rendered position, including quoted history |
| Already-inserted OpenSigWeave signature | Same-selected-signature content check | Resubmit the same message; test rotated/changed signatures separately |
| S/MIME/PGP signed/encrypted or message/rfc822 | Whole-message skip regression | Ensure raw message/signature is unchanged |
| Unicode / long folded headers | Lua/Python nested fixture | Check Subject, Received, Message-ID, encoded display names and complex folding |
| Oversized and malformed MIME | Explicit bounds and fail-closed test cases | Confirm no crash, loops, attachments changed or excessive CPU |
| DKIM / Mailcow processing | **Not covered by the unit/fixture tests** | Validate DKIM at a recipient outside Mailcow; inspect processing order |

**Important idempotency limit:** The adapter avoids another insertion if the **exact currently selected** signature is already present in the newly authored portion of the text representation. It does not offer an exactly-once guarantee across separate SMTP submissions, updated/rotating signatures or different independent body rewriters. Sender-controlled processing headers must never be treated as trusted proof of prior processing. A stronger exactly-once guarantee would need a trusted queue-side processing record.

**Signed mail / DKIM:** S/MIME/PGP content and attached `message/rfc822` structures are skipped. The adapter must run before DKIM signing and any downstream body mutation must be disabled or ordered appropriately. **Do not assume** that registering a `prefilter` alone proves the final DKIM signature is valid. Inspect the delivered message and authentication results on an external mailbox. Official Rspamd DKIM guidance: https://docs.rspamd.com/modules/dkim_signing/

**Failure behavior:** On engine errors or timeouts, multipart bodies are not rewritten; signature insertion is not guaranteed. Decide whether your beta should fail open (deliver unsigned-by-policy) or fail closed (temporary SMTP rejection). The current Rspamd adapter uses a fail-open model; it must not be described as fail-closed on engine outages. Test API 401/503, timeouts, TLS failures and key mismatch with a disposable mailbox. The outbound engine timeout is bounded to 0.5–15 seconds; the maximum response size is 1 KiB–1 MiB.

### Manual acceptance checklist (Mailcow beta)

1. Send **plain text only** from SOGo, Thunderbird and Outlook; verify readability and any text-to-multipart conversion.
2. Send **HTML only**, then **multipart/alternative** (plain+HTML), and verify no duplicated signature across representations.
3. Send UTF-8 **umlauts, emoji and accented names** with long, folded Subject/From headers.
4. Send text/plain/HTML using **quoted-printable and base64**; inspect the saved raw message.
5. Attach a **PDF, JPEG and ZIP**; compare SHA-256 before/after and inspect filenames and disposition.
6. Insert an **inline logo/image**, send it, inspect `Content-ID` and display at the recipient.
7. Send and receive a **nested multipart mixed → alternative → related** structure with an inline image and an attachment.
8. **Reply and forward** from SOGo, Outlook and Thunderbird; verify the new signature comes before quoted history.
9. Reprocess a message that already has a signature; test also **rotation to a different signature** and document any limitation.
10. Send **S/MIME/PGP signed and encrypted** mail and an attached `message/rfc822`; verify that OpenSigWeave does not modify them.
11. Cause **engine downtime, wrong API key, bad TLS** and restart Rspamd; verify delivery/retry policy and no mail loops.
12. Send to an **external DKIM-checking mailbox**; inspect delivered `DKIM-Signature`, authentication results, and whether other Mailcow footers are disabled.
13. Submit **oversized or unusually nested test mail** and monitor Rspamd CPU, memory, processing duration, logs and queue health.

Do not promote this adapter from beta to production until this list passes. Use the exact, recorded `dev` commit in the disposable environment; never silently copy untested files from a different branch.
