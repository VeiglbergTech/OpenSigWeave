# Mailcow / Rspamd adapter integration (staging only)

**Status:** Parser-backed multipart support is implemented on `dev` and tested with a Lua MIME-tree fixture followed by Python's MIME parser. No live Mailcow submission/DKIM acceptance has passed. Do **not** enable this on a production mail flow until the acceptance checks below succeed on a disposable Mailcow instance.

OpenSigWeave runs in its own Compose stack. The adapter runs inside Mailcow's Rspamd. Do not merge or replace Mailcow's bundled `rspamd.local.lua`.

## Install in a disposable Mailcow instance

Run from the Mailcow checkout directory; substitute `/path/to/OpenSigWeave` for the checkout of the *same tested OpenSigWeave commit*.

```bash
cd /opt/mailcow-dockerized
install -d -m 0755 data/conf/rspamd/plugins.d
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

For supported outgoing SMTP submissions, the adapter uses Rspamd's parsed MIME parts rather than regular expressions over encoded message bodies. It rewrites `text/plain` and `text/html` leaves of `multipart/alternative`, `multipart/related` and `multipart/mixed` messages (including nested combinations); non-text parts, binary attachments and inline image/CID parts are carried through unchanged. Text is decoded through Rspamd and re-encoded as UTF-8 quoted-printable. Recognized reply boundaries use the existing SOGo, Outlook and Thunderbird placement logic. Multipart messages larger than 20 MiB are skipped.

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
