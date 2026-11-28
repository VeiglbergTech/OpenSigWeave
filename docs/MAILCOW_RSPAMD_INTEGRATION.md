# Mailcow / Rspamd adapter integration (staging only)

**Status:** MIME rewrite regression tests run on `dev` with Lua fixtures parsed again by Python's real MIME parser (including nested sibling multipart, Unicode, long headers, inline CIDs and binary attachments). On 24 November 2026, an operator additionally reported successful CID delivery and correct SOGo 5.12.13 reply-signature placement on isolated Mailcow, plus an observed SMTP `4.7.1` temporary rejection on an OpenSigWeave injection failure. Those operator observations are **partial live acceptance only**: no sanitized raw-message evidence or post-injection external DKIM verification is archived here, and Outlook/Thunderbird, Milter outages, binary attachments and the complete usage matrix remain pending. Do **not** enable this on a production mail flow until the remaining acceptance checks succeed on a disposable Mailcow instance. Track cases in [mail usage acceptance matrix](MAIL_USAGE_ACCEPTANCE_MATRIX.md).

OpenSigWeave runs in its own Compose stack. The adapter runs inside Mailcow's Rspamd. Do not merge or replace Mailcow's bundled `rspamd.local.lua`.

## Normal release installation — one path for local and remote Mailcow

1. Deploy an **approved, published OpenSigWeave release image** with embedded
   release tag/revision metadata; development builds without that metadata
   deliberately provide no production adapter download commands. Log into
   **Admin → System → Mail Server**, select **Mailcow / Rspamd** and save an
   HTTPS engine endpoint reachable **from inside** the Mailcow Rspamd container.
   It can be an existing reverse proxy on the same host, another Docker
   network or another host. Use a valid, trusted TLS certificate.
2. On the Mailcow host, open a terminal **inside the existing Mailcow Compose
   checkout directory** (not necessarily /opt/mailcow-dockerized).
   Copy the **first** terminal block from the UI. It checks the published
   GitHub Release tag against the exact image revision, downloads **six small
   files** only from that immutable revision and independently validates the
   SHA-256 digest of every file before running the installer. No Git clone,
   new daemon, container or Docker port is needed.
3. At the masked terminal prompt, enter the existing
   **ENGINE_API_KEY from the protected OpenSigWeave .env on the application
   host**. For separate hosts, transfer it using an administrator-controlled
   encrypted channel or a secure password manager; do not copy all of .env,
   paste the key into the UI, shell command, URL, chat, logs or issue tracker.
   First-time preparation stores the key under
   /etc/opensigweave-mailcow/engine.key (0600, root-owned).
4. Preparation downloads the domain policy from
   GET /api/v1/mail/domain-policy using verified HTTPS and that engine key.
   A missing, invalid, unauthenticated or stale initial policy **blocks
   activation**; no missing snapshot is reported as operational.
   It installs four Lua modules and manages only a delimited section of the
   existing Mailcow rspamd.conf.local, leaving unrelated sections unchanged.
   For an existing **unmanaged** OpenSigWeave section, manually migrate it:
   the installer will refuse to silently replace it or customized Lua files.
   It checks rspamadm configtest before returning success.
5. Copy the **second** terminal block. It repeats validation, restarts only
   rspamd-mailcow, installs/enables a root-owned systemd timer to sync policy
   every five minutes and prints an explicit **SMTP still untested** notice.
   A host without systemd needs an equivalent root cron job and does not
   silently report activation successful. No public controller port is needed.

**Managed Rspamd files:**
data/conf/rspamd/plugins.d/opensigweave.lua,
data/conf/rspamd/lua/opensigweave_mime.lua,
data/conf/rspamd/lua/opensigweave_cid.lua and
data/conf/rspamd/lua/opensigweave_domain_policy.lua.
The installation also creates a local
data/conf/rspamd/local.d/opensigweave-domain-policy.json snapshot, a protected
engine-key file, a root-managed installation state and a host-side Python
synchronization helper/systemd timer. It does **not** overwrite
rspamd.local.lua, Mailcow's Docker Compose, Postfix, networks, port mappings or
unrelated Rspamd filters.

The managed block uses global failure_policy = "enabled" with
domain_policy_file set. For unavailable OpenSigWeave (Mailcow/Rspamd healthy),
Signature required **Off** uses an authenticated local exemption to send
unsigned. **On**, unknown or invalid/missing local policy requests a temporary
SMTP 4xx rejection. A stale local Off exemption cannot be revoked while the
sync path is offline. Inspect changes with the policy status command below
and compare to Admin → Domains' desired policy_version before relying on
any changed requirement.

From the same Mailcow directory, inspect with:

~~~bash
sudo python3 /usr/local/lib/opensigweave-mailcow/mailcow_setup.py status --mailcow-root "$PWD"
sudo python3 /usr/local/lib/opensigweave-mailcow/mailcow_setup.py sync --mailcow-root "$PWD"
systemctl status opensigweave-mailcow-policy.timer
~~~

Stop future synchronization with
sudo systemctl disable --now opensigweave-mailcow-policy.timer.
An intentional rollback is:
sudo python3 /usr/local/lib/opensigweave-mailcow/mailcow_setup.py rollback --mailcow-root "$PWD".
It first checks managed file hashes and refuses administrator edits. The
protected engine secret and last-known-good local domain snapshot are retained
for deliberate operator cleanup, not silently deleted.

After the first real API-key-authenticated adapter request OpenSigWeave
persists setup completion and collapses the configuration behind
**Reconfigure Mail Server**. Active / Active Xm / Active Xh / Unverified are
activity timestamps, not verified signature insertion. Optional advanced
module checks may report Error when a required symbol is affirmatively absent;
they require an existing private controller path, never a public endpoint.
Real SMTP acceptance and final received HTML, plain text, MIME and DKIM
verification remain separate release gates.

The admin API never returns ENGINE_API_KEY.
Do not infer SMTP delivery, inline CID rendering, valid DKIM or safety of
production rollout from a successful HTTP request, mocked test or
rspamadm configtest.

## Independent missing-plugin guard (experimental, opt-in; isolated Mailcow only)

**Reproduced fail-closed gap, 24 November 2026:** with `rspamd-mailcow` running but `plugins.d/opensigweave.lua` disabled, Postfix SOGo submission accepted an email **without a signature**. The previous `milter_default_action=tempfail` did not protect this case because the Milter itself remained reachable. Issue #14 remains open.

A separate Rspamd postfilter in `rspamd/opensigweave_guard.lua` can reject **authenticated** messages if `OPENSIGWEAVE_INJECT` is not registered (or symbol registration cannot be inspected). This deliberately checks **plugin presence**, not successful delivery or all possible plugin malfunction. A legitimate `NONE` rule and signed/encrypted skip are unaffected when the injector itself is registered. Unauthenticated inbound SMTP is excluded.

In an **isolated environment**, install and enable the guard independently of the signature-injection module:

```bash
cd /opt/mailcow-dockerized
install -m 0644 /opt/OpenSigWeave/rspamd/opensigweave_guard.lua \
  data/conf/rspamd/plugins.d/opensigweave_guard.lua
```

Add once to the existing `data/conf/rspamd/rspamd.conf.local`:

```ucl
opensigweave_guard {
    enabled = true;
}
```

Validate before restart:

```bash
docker compose exec -T rspamd-mailcow rspamadm configtest
docker compose restart rspamd-mailcow
docker compose logs --tail=80 rspamd-mailcow
```

**Live acceptance:** (1) check normal authenticated SOGo delivery with a signature, (2) disable *only* `opensigweave.lua`, restart Rspamd, and verify SOGo's SMTP DATA returns a 4xx with `SIGNATURE_PLUGIN_NOT_LOADED` and **no accepted/queued message**, (3) verify unauthenticated inbound remains unchanged, (4) restore the module and retest a successful signed message. If the guard fails to load or is disabled too, this protection disappears: Postfix cannot detect a successfully responding but semantically incomplete Rspamd Milter. This is **not a production-certified independent Postfix enforcement gate**. Real fail-closed against arbitrary missing Rspamd checks requires a separate, fail-closed mechanism at the submission boundary.

## Legacy manual integration (advanced, not the normal release installer)

For manual installation when the tested OpenSigWeave checkout is already available on the Mailcow host, run from the Mailcow checkout directory; substitute `/path/to/OpenSigWeave` for the checkout of the *same tested OpenSigWeave commit*.

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

For the managed installer use its hash-checked rollback action above. For manually created, unmanaged configurations only, remove exactly the own plugin/configuration section, validate with rspamadm configtest and restart Rspamd. Preserve unrelated filters and verify DKIM and inbound mail.

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


## Failure policies: disabled and enabled (issue #14)

Set `failure_policy = "disabled"` or `failure_policy = "enabled"` in the
Rspamd `opensigweave` section. `standard` and `tempfail` remain aliases
for `enabled`; `fail_open` aliases `disabled`. The independent strict
receipt service and Postfix Milter have been removed.

On the Mailcow host, check the installed modules after restarts/updates:

~~~bash
python3 /opt/OpenSigWeave/scripts/check_mailcow_modules.py \
  --mailcow-dir /opt/mailcow-dockerized --policy enabled
~~~

Exit 0 = module/configuration checks passed, 1 = missing/disabled, 2 = unknown.
Run the check periodically using host monitoring. This is not a live SMTP
injection test and cannot enforce a missing-module failure. See the
[Mailcow failure policy and health-check guide](MAILCOW_FAILURE_POLICIES.md).

## Enabled fail-closed mode (issue #14, not yet production-ready)

The Rspamd adapter supports `failure_policy = "disabled"` (also `fail_open`) and `failure_policy = "enabled"` (also `standard` or `tempfail`). In enabled mode, engine HTTP failures, invalid API responses, request scheduling errors, failures to decode flat mail, unsafe multipart rewrite failures, unexpected synchronous/asynchronous Lua callback exceptions and failed `task:set_message()` requests set Rspamd's **soft reject** pre-result. A correlation ID appears in the SMTP text and Rspamd logs. Deliberate empty signatures, disabled reply-signing and protected S/MIME/PGP or embedded messages are excluded. Rollback is changing the option to `fail_open` and restarting Rspamd, after verifying its config.

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
OSW_ALERT_STATUS_FILE=/var/lib/opensigweave-alerts/status/status.json
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

For **System → Support Alerts** in the OpenSigWeave administration portal,
create the notifier's `status/` directory and mount **only that directory**
into the application container as read-only. Configure `OSW_ALERT_STATUS_FILE`
inside the application with the mounted `status.json` path. Ensure the
application process can read the exported file (the notifier creates it with
mode 0600, so explicit permissions/ownership or an ACL may be required).
The notifier writes aggregate counts and error classes atomically every few
seconds during log ingestion and after its periodic `health` and `flush`.
Stale or unavailable snapshots are marked degraded; they do not certify that
Postfix/Rspamd or the alert relay works. Never mount the notifier's SQLite
state or SMTP secrets into the app. The API remains administrator-only.

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
    failure_policy = "enabled";
}
```

Install `opensigweave_cid.lua` alongside `opensigweave_mime.lua`. Only images already in the managed local asset store are allowed. Upload files from the editor or choose **Load from URL**, which performs one pinned-DNS, verified HTTPS import with SSRF protections and no redirects; subsequent sending is independent of the image source server. The backend limits each image to 2 MiB, at most six distinct images and 4 MiB combined. The adapter enforces its own size/MIME limits. Unknown external signature images are rejected by the inline-image API rather than quietly left as external links.

**Compatibility gate:** Test flat HTML and text, nested `multipart/alternative` / `multipart/related` / `multipart/mixed`, replies, forwards, image collisions, current attachments and original inline images in actual Mailcow/Rspamd. Verify that `Content-Type` and `Content-Transfer-Encoding` reflect the wrapped message in the delivered raw EML, that DKIM still validates, that S/MIME/PGP exclusions remain unchanged, and that SOGo/Outlook/Thunderbird all render the CID attachments without needing the OpenSigWeave public URL. Offline Lua/Python MIME fixture tests alone are not proof of those live requirements. The experimental Postfix Milter can enable `OPENSIGWEAVE_INLINE_IMAGES=cid` and
Stalwart's in-process hook can enable `STALWART_INLINE_IMAGES=cid`.
Both validate the CID payload and preserve plain-only messages without orphan attachments.
Issue #8 remains open until live SMTP/DKIM and recipient-client acceptance passes.


### CID and SOGo reply regression checks (24 November 2026)

The repository's Rspamd configuration example enables CID and sets
`max_response_size = 6291456`. **Existing Rspamd deployments retain their own
configuration**: an omitted `inline_images` option still means `external`.
To use CID in Mailcow, explicitly configure `inline_images = "cid"`, install
`opensigweave_cid.lua` next to the adapter and reload/restart Rspamd.
Merely selecting or uploading an image in the web UI does not enable CID.
Verify the *received raw email*: its outer MIME type should be
`multipart/related`, the new HTML image reference must start with `cid:`,
and the corresponding MIME part needs matching `Content-ID` and
`Content-Disposition: inline`. The public asset URL may remain in old quoted
messages and is not evidence that the *new* signature failed to use CID.

SOGo may send a flat text reply that OpenSigWeave upgrades to
`multipart/alternative`, or an existing alternative with an attribution after
HTML `<br>` elements. In both representations the new signature is inserted
before the first `Am … schrieb` attribution, leaving older quoted signatures
in their original context. The fixture uses a nested German SOGo conversation.
Check a live SOGo submission as well: CI's MIME parser cannot validate Mailcow's
Milter application or the recipient's actual rendering.
