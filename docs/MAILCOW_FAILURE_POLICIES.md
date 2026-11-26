# Mailcow SMTP failure policies — Issue #14

**Development implementation only. Not yet certified on a live Mailcow installation.**
Strict requires a dedicated final Postfix Milter, independent of both Rspamd
OpenSigWeave Lua modules. Never enable it globally before isolated acceptance.

## Configuration modes

Set `failure_policy` in the existing Rspamd `opensigweave` UCL block.

| Mode | Behavior |
| --- | --- |
| `disabled` | OpenSigWeave does not request SMTP rejection on signature processing errors. Other Postfix/Milter failures may still reject mail. |
| `standard` | Previous `tempfail` behavior: identified technical failures receive SMTP 4xx. Configure the authenticated Postfix submission Milter to tempfail if Rspamd is unavailable. Missing both OpenSigWeave modules can still allow unsigned delivery. |
| `strict` | Standard plus an independent EOM Milter after Rspamd; it rejects any authenticated message for which the injector did not supply a fresh, one-time, authenticated proof of signing or an intentional skip. Missing both Lua modules cannot supply that proof. |

Existing values `fail_open` and `tempfail` remain supported as aliases for
`disabled` and `standard`. The default is `disabled` (the old fail-open default);
existing installations do **not** silently switch to strict. No Web UI toggle.

`standard` and `disabled` example:

~~~ucl
opensigweave {
    api_url = "https://signature.example.test/api/v1/engine/signatures/";
    engine_api_key = "<existing-engine-key>";
    failure_policy = "standard"; # or "disabled"
}
~~~

The optional `opensigweave_guard` module respects `disabled`, so a missing
injector does not independently reject when disabled. The existing Postfix
`milter_default_action` remains separate: `disabled` cannot override a global
Postfix decision to temporary-reject an unavailable Rspamd Milter.

## Strict architecture

The Rspamd injector generates an event only after successful message mutation
(or an explicitly intentional `NONE`, disabled-reply or protected-message skip).
It sends a receipt with the Postfix queue ID, SASL identity, envelope sender
and limited decision enum to `scripts/opensigweave_strict_gate.py`, protected by
a separate 32+ character shared secret. There are **no message bodies** and no
sender-controlled email headers in the proof.

The **independent last-in-chain Milter**
`scripts/opensigweave_strict_milter.py` runs after Rspamd and checks/consumes
the receipt. Missing receipt, an unhealthy receipt service, a malformed queue
ID, an incorrect sender or a service timeout all result in temporary SMTP 451.
Receipts expire after 90 seconds and are consumed exactly once.

**Do not use `smtpd_end_of_data_restrictions` for this proof check.**
Postfix can evaluate that restriction before Rspamd's Milter EOM callback,
causing legitimate signed messages to be rejected. The second Milter needs
to be **after Rspamd in the Milter chain**, not before it.

### 1. Install the independent receipt service

Create a dedicated process/container on an isolated Mailcow Docker network,
reachable by the Postfix and Rspamd containers. Python 3.11+ standard library
is sufficient for this service:

~~~dotenv
OSW_STRICT_BIND=0.0.0.0
OSW_STRICT_RECEIPT_PORT=10044
OSW_STRICT_POLICY_PORT=10043
OSW_STRICT_RECEIPT_KEY=<unique-generated-secret-with-at-least-32-characters>
~~~

~~~bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
python3 scripts/opensigweave_strict_gate.py
~~~

Run as a supervised service with automatic restart; keep both ports internal.
The receipt HTTP endpoint (10044) accepts proofs using a secret; the internal
policy protocol (10043) is used only by the independent Milter.

### 2. Configure the signature injector

Add to the existing `opensigweave` Rspamd UCL section:

~~~ucl
failure_policy = "strict";
strict_receipt_url = "http://opensigweave-strict-gate:10044/receipt";
strict_receipt_key = "<same-receipt-service-secret>";
~~~

Retain the existing HTTPS engine URL/key and other options. Do **not** publish
secrets in Git or share them with Web users. The injector does not activate
strict without both URL and sufficiently long key. This alone does NOT provide
independent fail-closed enforcement; Step 3 is essential.

### 3. Install the second, independent Milter

Install Python `pymilter==1.0.5` and its host `libmilter` development/runtime
libraries in an isolated Python environment. This is an **optional external
deployment dependency**, not a requirement for the OpenSigWeave Web app.

Configure the process from the tested OpenSigWeave checkout:

~~~dotenv
OSW_STRICT_MILTER_SOCKET=inet:10045@0.0.0.0
OSW_STRICT_GATE_HOST=opensigweave-strict-gate
OSW_STRICT_POLICY_PORT=10043
~~~

~~~bash
python3 scripts/opensigweave_strict_milter.py
~~~

Do not publish port 10045 publicly. This process should restart independently
of Rspamd and OpenSigWeave.

### 4. Configure Postfix submission ONLY

On each authenticated Submission service (587, 465, SOGo/588, applicable
HAProxy listeners), add the strict Milter **after the existing Rspamd Milter**.
Merge with all already configured Milters and maintain current authorization
restrictions. A schematic service-specific `master.cf` option is:

~~~text
-o smtpd_milters=inet:rspamd:9900,inet:opensigweave-strict-milter:10045
-o milter_default_action=tempfail
~~~

These are **not a drop-in Mailcow configuration**. Determine the effective
service and its supported override location for the installed Mailcow version
before editing; never change inbound port-25 Milters. Rspamd down, strict
Milter down and broken Milter links must all tempfail.

Verify effective settings rather than merely the global `main.cf`:

~~~bash
docker compose exec -T postfix-mailcow postconf -P
docker compose exec -T postfix-mailcow postconf -n smtpd_milters milter_default_action
docker compose exec -T rspamd-mailcow rspamadm configtest
~~~

An SMTP-DATA 451 without actual Milter/queue evidence is not sufficient to
approve production deployment.

## Required isolated end-to-end acceptance

1. Signed authenticated email: Rspamd must inject a signature, the independent
   Milter must consume a receipt and only then may Postfix return SMTP 250.
2. Intentionally skipped `NONE`, disabled-reply and S/MIME/PGP content: an
   explicit skip receipt must let the correct message through unchanged.
3. Disable **both** OpenSigWeave Lua modules while Rspamd itself stays healthy:
   every authenticated test must receive SMTP 451 without `250 queued`.
4. Stop each of Rspamd, OpenSigWeave, PostgreSQL, the receipt service and the
   independent Milter separately: every required message must tempfail.
5. Confirm unaffected unauthenticated inbound port-25 mail, unaffected DKIM
   and attachment hashes, and correct Outlook/SOGo/Thunderbird behavior.
6. Test all actual Submission listeners and restore the plugins immediately.
   Record redacted Postfix/Rspamd logs, exact build SHA and queue IDs.

**Rollback:** On test systems change policy to `standard`, remove only the
strict second Milter from the authenticated service chain (retain Rspamd and
other Milters), and restart/reload safely after configuration tests. Never
leave `strict` enabled without the independent Milter: that would eliminate
the promised protection.

## Limits

Only authenticated SMTP transactions through listeners actually configured
with the second Milter are protected. Postfix non-SMTP queue injection and
uncovered relays require separate mechanisms. Compromise of the shared proof
secret or of the injector can forge receipts. A successful Rspamd mutation
receipt is not cryptographic proof of final DKIM content, which must still be
checked at recipients. **Do not claim live-tested production readiness on CI
results alone.**
