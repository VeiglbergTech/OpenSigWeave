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
install -m 0644 /path/to/OpenSigWeave/rspamd/opensigweave_cid.lua data/conf/rspamd/lua/opensigweave_cid.lua
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

**Failure behavior:** On engine errors or timeouts, multipart bodies are not rewritten; signature insertion is not guaranteed. Decide whether your beta should fail open (deliver unsigned-by-policy) or fail closed (temporary SMTP rejection). The adapter defaults to fail-open. An experimental `failure_policy = "tempfail"` opt-in covers the Lua-side error paths only; it does not guarantee fail-closed when Rspamd is offline. Test API 401/503, timeouts, TLS failures and key mismatch with a disposable mailbox. The outbound engine timeout is bounded to 0.5–15 seconds; response size is bounded to 1 KiB–1 MiB by default, with up to 8 MiB exclusively in opt-in CID mode.

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


## Experimental fail-closed opt-in (issue #14, not yet production-ready)

The Rspamd adapter accepts `failure_policy = "fail_open"` (default) or `failure_policy = "tempfail"`. In the latter mode, engine HTTP failures, invalid API responses, request scheduling errors, failures to decode flat mail, unsafe multipart rewrite failures, unexpected synchronous/asynchronous Lua callback exceptions and failed `task:set_message()` requests set Rspamd's **soft reject** pre-result. A correlation ID appears in the SMTP text and Rspamd logs. Deliberate empty signatures, disabled reply-signing and protected S/MIME/PGP or embedded messages are excluded. Rollback is changing the option to `fail_open` and restarting Rspamd, after verifying its config.

This **does not guarantee** that Postfix rejects mail when Rspamd itself is offline or when the adapter could not load. Inspect the effective submission/SMTPS service (not only `main.cf`):

```bash
docker compose exec postfix-mailcow postconf -n smtpd_milters milter_default_action
docker compose exec postfix-mailcow postconf -P | grep -E '(^submission/|^smtps/).*(smtpd_milters|milter_default_action)'
```

For a **read-only, fail-on-misconfiguration** check of the effective Postfix service overrides, run from the Mailcow Compose directory (the OpenSigWeave checkout is on the host):

```bash
python3 /path/to/OpenSigWeave/scripts/audit_mailcow_submission.py \\
  --postconf-command "docker compose exec -T postfix-mailcow postconf" \\
  --expected-milter rspamd
```

The check requires every enabled `submission`, `submissions` or `smtps` inet service to reference the expected Rspamd Milter endpoint and to use `milter_default_action=tempfail`. It resolves inherited `$smtpd_milters` references, fails on unknown variable expansions, and warns if port-25 `smtp/inet` also inherits `tempfail`. The expected endpoint hint must match the actual deployment (e.g. supply a literal address if your service doesn't call it `rspamd`). No configuration is changed. **A passing audit does not prove that the Rspamd Lua module is loaded, that Postfix can reach Rspamd, or that 451 reaches the SMTP client.**

If either authenticated submission service has an override such as empty `smtpd_milters`, global settings do not protect it. Test and configure the actual SMTP entrypoint to call Rspamd with temporary failure on Milter unavailability. Do not change the inbound port-25 policy blindly. In an **isolated test mailbox/domain**, induce HTTP 401/422/503, failed TLS, timeouts, invalid JSON, lost OpenSigWeave database, Rspamd shutdown, and Postfix-to-Milter disconnection. Capture the actual SMTP exchange: there must be **no `250 queued`** for signature-required mail after the failure, then a single successful injection after recovery. Test SOGo, Outlook, Thunderbird, DKIM and attachment hashes. Clients may show generic retry errors rather than Rspamd's text.

#### Controlled SMTP DATA acceptance gate (test mailbox only)

After staging the Milter, run this probe **only on an isolated test domain** against the actual authenticated Mailcow port 587/465. Store the test mailbox password in an environment variable (not an argument). The probe sends one small test message and never retries automatically:

```bash
export OSW_TEST_SMTP_PASSWORD='your-disposable-test-account-password'
# While OpenSigWeave is deliberately unavailable OR Rspamd is deliberately stopped:
python3 /path/to/OpenSigWeave/scripts/mailcow_smtp_probe.py \\
  --host mail.example.test --port 587 \\
  --username test@mail.example.test --sender test@mail.example.test \\
  --recipient isolated-test@mail.example.test \\
  --expect tempfail --confirm-disposable-test
# Restore the service and repeat with --expect accepted.
unset OSW_TEST_SMTP_PASSWORD
```

The probe passes the failure case **only** on an actual 4xx reply at the SMTP DATA stage (not an RCPT failure or network timeout). If the message is accepted, it warns that it may have been queued/delivered. The recovery check confirms SMTP acceptance only: inspect the received raw EML for exactly one signature, valid DKIM, intact attachments and MIME structure. Repeat failure checks separately for engine HTTP/TLS/DB outages, Rspamd shutdown and Milter disconnect; inspect genuine SOGo/Outlook/Thunderbird retry UX. Use unique controlled messages and monitor the test queue. No server outages are induced by the probe itself.

An **independent, opt-in support notifier** now exists at `scripts/opensigweave_support_notifier.py` with persistent SQLite outbox, retry after SMTP failure, rate limiting (one failure summary per error class per 15 minutes), deduplication by random event ID and a Docker-container outage probe. It is not automatically deployed and must send through a **separate authenticated, verified-TLS SMTP relay**. Its recovery event means successful signature mutation was observed, not that the full SMTP chain is certified healthy. Optional user-facing out-of-band notifications, integrated admin UI status and real Mailcow SMTP end-to-end acceptance are still outstanding; leave issue #14 open until these are validated.

### Independent support notifier (test deployment)

Set up a dedicated service host with Python 3.12+ and Docker CLI access to the Mailcow host. Place the notifier's database on a persistent, backed-up filesystem separate from Rspamd and the OpenSigWeave database. Use an **independent** relay, never the same Mailcow submission path being monitored. Configure a root-owned environment file (mode 0600) containing:

```dotenv
OSW_ALERT_DATABASE=/var/lib/opensigweave-alerts/alerts.sqlite3
OSW_ALERT_SUPPORT_TO=support@example.org
OSW_ALERT_FROM=alerts@example.org
OSW_ALERT_SMTP_HOST=smtp.independent-relay.example
OSW_ALERT_SMTP_PORT=587
OSW_ALERT_SMTP_USER=REPLACE_WITH_RELAY_ACCOUNT
OSW_ALERT_SMTP_PASSWORD=REPLACE_WITH_RELAY_SECRET
```

Do not store real credentials in Git. Import the environment from a restricted service manager secret or `EnvironmentFile`; never pass the password as a command-line argument. Run these commands from the repository checkout on that service host:

```bash
# Ingest Rspamd structured error/success logs continuously.
docker logs --timestamps --since 5m -f rspamd-mailcow-1 | python3 scripts/opensigweave_support_notifier.py ingest

# From a separate systemd timer/cron job every 1 minute:
python3 scripts/opensigweave_support_notifier.py health --container rspamd-mailcow-1
python3 scripts/opensigweave_support_notifier.py flush

# Inspect pending notifications without exposing mail contents:
python3 scripts/opensigweave_support_notifier.py status
```

Supervise the continuous log reader with a restart-on-failure policy. A Docker CLI credential has host-level privileges and must be protected. If the notifier is remote, access Docker over a separately secured channel rather than exposing its socket unauthenticated. `health` treats inaccessible Docker state as unavailable and records one synthetic event per five-minute bucket; verify the correct container ID on your installation. The SMTP relay only accepts implicit TLS on port 465 or STARTTLS on port 587, with certificate verification. Notifications never contain the original email body, sender header or engine token.

Simulate repeated failure events and a relay outage; verify a single grouped pending notification survives a notifier restart and is delivered after the relay returns. After a logged successful signature mutation, validate the explicitly qualified recovery notice. Do not expect this reporter to make SMTP acceptance decisions: only Postfix/Milter and the Rspamd failure policy can do that. Review log retention and notifier outage recovery (the example tail reads the last five minutes) before production use.

## Opt-in CID signature images (issue #8, experimental)

The Rspamd adapter supports two modes:
- `inline_images = "external"` (default): existing externally linked images, no change to message serialization.
- `inline_images = "cid"`: API uses the authenticated `X-OpenSigWeave-Inline-Images: cid` request header and sends locally stored, validated signature images as bounded base64 payloads. The adapter embeds these directly as `Content-Disposition: inline` MIME parts, assigns each a stable content-addressed `Content-ID`, rewrites the signature HTML to use `cid:` references, and wraps the existing message into `multipart/related` without changing original MIME leaves. It does not download unknown remote URLs at sending time.

For a **disposable test domain**, configure:

```ucl
opensigweave {
    api_url = "https://signature.example.org/api/v1/engine/signatures/";
    engine_api_key = "REPLACE_WITH_DISTINCT_RANDOM_ENGINE_KEY_AT_LEAST_32_CHARS";
    timeout = 5.0;
    inline_images = "cid";
    max_response_size = 6291456;
    failure_policy = "tempfail";
}
```

Install `opensigweave_cid.lua` alongside `opensigweave_mime.lua`. Only images already in the managed local asset store are allowed. Upload files from the editor or choose **Load from URL**, which performs one pinned-DNS, verified HTTPS import with SSRF protections and no redirects; subsequent sending is independent of the image source server. The backend limits each image to 2 MiB, at most six distinct images and 4 MiB combined. The adapter enforces its own size/MIME limits. Unknown external signature images are rejected by the inline-image API rather than quietly left as external links.

**Compatibility gate:** Test flat HTML and text, nested `multipart/alternative` / `multipart/related` / `multipart/mixed`, replies, forwards, image collisions, current attachments and original inline images in actual Mailcow/Rspamd. Verify that `Content-Type` and `Content-Transfer-Encoding` reflect the wrapped message in the delivered raw EML, that DKIM still validates, that S/MIME/PGP exclusions remain unchanged, and that SOGo/Outlook/Thunderbird all render the CID attachments without needing the OpenSigWeave public URL. Offline Lua/Python MIME fixture tests alone are not proof of those live requirements. Other mailserver adapters still need CID support, so issue #8 remains open until platform and client acceptance tests pass.
