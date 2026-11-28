# Mailcow/Rspamd: durable offline domain signature requirements

**Scope:** Authenticated outbound submissions when OpenSigWeave is unavailable but **Mailcow/Rspamd and the Milter remain healthy**. This feature does not handle complete Rspamd failures. It needs no new permanent daemon or Docker container.

## Domain behavior

| Domain setting | Engine healthy | Technical API failure, timeout, 401/403/5xx, invalid response |
| --- | --- | --- |
| **Signature required: On** | Normal rules and signature insertion | SMTP 4xx temporary rejection requested through Rspamd |
| **Signature required: Off** | Normal rules and signature insertion | Accept without an OpenSigWeave signature **if a trusted local exemption exists** |
| Unknown sender domain / missing, corrupt or untrusted policy | Normal rules | Temporary rejection if offline-policy mode is enabled |

The setting is domain-wide. The lookup uses only the authenticated SMTP envelope-sender domain; a spoofable message From header is not consulted. Domain matching is exact and case-insensitive; a parent domain does not exempt subdomains. Mailcow sender authorization, aliases and delegated send-as remain independently enforced. Unauthenticated inbound traffic is unaffected.

An intentionally empty signature, a disabled reply signature or a deliberate MIME skip is not a technical outage. During API failure, the adapter must leave message bodies, original selector markers, headers and MIME unchanged.

## V2 snapshot: durable last known good

The authenticated HTTPS endpoint **GET /api/v1/mail/domain-policy** now exports this schema:

~~~json
{
  "format": "opensigweave-domain-policy-v2",
  "generated_at": 1000000000,
  "policy_version": "<64 lowercase hex characters>",
  "domains": {"example.test": false, "required.test": true}
}
~~~

The version is SHA-256 of the canonical ASCII JSON domain map (sorted keys, compact separators, ASCII escapes); unlike generation time, this fingerprint remains stable between polls unless a domain decision changes. **The digest is an identifier, not a digital signature or independent proof of server authenticity.**

The host helper downloads only via authenticated HTTPS with valid TLS and the existing Engine API key. It refuses redirects, malformed responses, duplicate keys, old/replayed network results and digest mismatches. It atomically installs an fsync-protected mode-0644 snapshot in a root-owned, non-group-writable Mailcow directory, using a root-only key file. The Rspamd worker can read but cannot write the file. Restrict access to all administrative paths and scripts; this trust model does not detect a malicious administrator/root compromise.

A securely installed **v2 policy has no rolling 24-hour expiry**. A previously verified Off exemption remains effective after days of API downtime and Rspamd restarts. On failure Rspamd evaluates the protected local snapshot without asking OpenSigWeave again. Invalid structure, unknown domains and missing/untrusted data are never treated as exemptions.

Old v1 snapshots remain readable only during their original bounded lifetime and can expire after 24 hours. Before relying on long outages, upgrade the API, sync helper and Rspamd Lua module and install/confirm a v2 snapshot. A failed upgrade/sync must not replace the last known good.

## Installation and reliable synchronization

The standard **Admin → System → Mail Server** Mailcow workflow now installs
the domain policy module, valid first snapshot and five-minute systemd
synchronization timer as part of its verified two-block host setup. Follow
[Normal Mailcow installation](MAILCOW_RSPAMD_INTEGRATION.md)
rather than copying an independently unversioned script.

On Mailcow run:

~~~bash
sudo python3 /usr/local/lib/opensigweave-mailcow/mailcow_setup.py status --mailcow-root "$PWD"
sudo python3 /usr/local/lib/opensigweave-mailcow/mailcow_setup.py sync --mailcow-root "$PWD"
systemctl status opensigweave-mailcow-policy.timer
~~~

The Python helper keeps the last known valid v2 snapshot across outages; its
ACK includes the policy version. Compare that ACK to Admin → Domains' desired
version on **each** Mailcow host. Any Off → On transition must remain
**pending** until confirmed on every relevant host. An offline host can
continue honoring its former Off exemption indefinitely.

To disable automatic updates explicitly use
sudo systemctl disable --now opensigweave-mailcow-policy.timer.
The snapshot is retained, and disabling sync does **not** magically revoke
previous exemptions. Existing manual/legacy installations require a
deliberate migration; the setup tool will not overwrite their unmanaged
Rspamd section or customized Lua files. If the machine does not use systemd,
configure an equivalent secured root cron schedule manually; activation
will not falsely report automatic synchronization enabled.

The managed Rspamd section enables domain-specific decisions and global
failure_policy = "enabled". A domain without a valid explicit Off exemption
temporarily rejects on an OpenSigWeave failure. This does **not** cover
Rspamd/Milter outages or bypasses.

## Changes, version acknowledgment and emergency strictness

1. Save a domain change, then obtain the **desired version** from Admin → Domains (or Download mailserver policy).
2. On **each Mailcow host**, perform authenticated synchronization using the command above plus **--expect-version FULL_64_CHARACTER_HASH**. Verify an ACK with exit code 0. For a purely offline read of installed state, use **--status --expect-version FULL_64_CHARACTER_HASH**.
3. If the desired version is not served or installed, the helper reports a prominent WARNING and exit code 3. Treat the stricter change as **pending**, not enforced. Archive the host-side version/ACK and review scheduled sync failures.
4. Only regard the new policy as operational after every affected mailserver acknowledges the intended version. A version is content-based and can recur if toggles are reversed; compare to the current desired map.

**Security limitation:** An Off → On change during a loss of OpenSigWeave or the sync path cannot revoke an already trusted old Off exemption. The mailserver may continue allowing that domain indefinitely, until a trusted update or operator intervention. This is the unavoidable tradeoff of durable agentless snapshots. No immediate cross-host revocation, cryptographic proof of an offline local file, or automatic web UI confirmation of mailserver state is claimed.

For an urgent stricter requirement, set a **Mailcow-host-owned emergency override** in the same Rspamd opensigweave configuration section and restart/reload Rspamd after config validation:

~~~ucl
domain_policy_force_required = ["example.test", "sub.example.test"];
~~~

These exact local On overrides take priority over a stale Off snapshot. A malformed override fails closed. Remove it only after the desired v2 policy has reached every mailserver and was acknowledged. Strict-domain overrides do not change legitimate sender authorization.

## Existing global Failure Policy

With the domain_policy_file option omitted, legacy behavior is unchanged: disabled (or fail_open) accepts unsigned after a technical failure, enabled (or standard/tempfail) requests temporary rejection. The retired strict mode is not restored.

With the domain_policy_file option set, trusted Off takes precedence even if the global mode is enabled; On/unknown/missing/invalid local policy takes precedence even if global mode is disabled. Neither this policy nor the optional guard can reject mail if both Rspamd Lua modules are absent, Rspamd itself is down, or the Milter is bypassed. Changes to Postfix inbound port 25 are outside this task.

## Troubleshooting, rollback and mandatory SMTP evidence

If optional mail suddenly defers, verify the v2 file, root directory and file permissions, matching envelope sender domain, optional strict overrides, loaded Rspamd modules and the actual host-side ACK. A 401/403 suggests API key/permissions issues; 500/503 or timeout suggests backend/network issues. Failed downloads, malformed JSON and version mismatch do not replace the last-known-good snapshot. Correct a bad toggle in OpenSigWeave, synchronize and acknowledge on each mailserver. Remove emergency overrides only after confirming the new policy. A rollback to global-only mode changes delivery guarantees and needs an explicit administrative decision, not a silent fallback.

On a **disposable Mailcow only**, verify actual authenticated SMTP acceptance for Off and temporary rejection for On on engine outages; assert no required-domain mail is accepted or queued. Repeat with API 401/403/500/503, timeouts, malformed JSON, unknown/corrupt policies, exact subdomains, >24h outage, Rspamd restart, local strict overrides and both toggle directions with version ACK. After recovery validate signature rules and exactly one signature, original attachments and MIME, sender authorization, inbound isolation, and externally **valid DKIM**. Do not induce outages on production. Lua mocks and green CI are **not** full Mailcow/Milter acceptance evidence.

See [global failure policy](MAILCOW_FAILURE_POLICIES.md) and [Rspamd integration](MAILCOW_RSPAMD_INTEGRATION.md).
