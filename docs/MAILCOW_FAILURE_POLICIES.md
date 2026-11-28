# Mailcow failure policy and module health checks

> Domain-specific offline enforcement is **opt-in**. Configure the local
> policy file and synchronize it on the mailserver before relying on
> per-domain exemptions. See [Domain-specific offline policy](MAILCOW_DOMAIN_FAILURE_POLICIES.md).
> The global `enabled`/`disabled` mode remains compatible with existing
> installations that have not enabled a local snapshot.


OpenSigWeave supports two Rspamd failure policies.

| Policy | Technical injection failure | Missing injector while guard loads | Both Rspamd modules missing |
| --- | --- | --- | --- |
| `disabled` | No OpenSigWeave-specific SMTP rejection | Guard intentionally disabled | Can silently accept unsigned |
| `enabled` (recommended) | SMTP temporary rejection | Guard requests SMTP temporary rejection | Can silently accept unsigned |

The default remains `disabled` for existing configurations. To enable
temporary rejection, configure these sections in Mailcow's existing
`data/conf/rspamd/rspamd.conf.local`:

~~~ucl
opensigweave {
    api_url = "https://signature.example.test/api/v1/engine/signatures/";
    engine_api_key = "<your-existing-engine-key>";
    failure_policy = "enabled";
}
opensigweave_guard {
    enabled = true;
}
~~~

Do not replace your real API URL or key with the sample values. Legacy
`standard` and `tempfail` are accepted aliases for `enabled`; `fail_open`
aliases `disabled`. The former `strict` value has been **removed** and must
be replaced with `enabled`. If a standalone strict gate and strict Postfix
Milter were installed, remove them from Mailcow's authenticated submission
Milter chain as part of the migration, leaving Rspamd and other Milters intact.

## Host-side module health checks

Run on the **Mailcow host**, outside the OpenSigWeave application container:

~~~bash
python3 /opt/OpenSigWeave/scripts/check_mailcow_modules.py \
  --mailcow-dir /opt/mailcow-dockerized --policy enabled
~~~

The check uses Docker Compose to confirm the Rspamd container can read
`opensigweave.lua`, `opensigweave_guard.lua`, `opensigweave_mime.lua` and
`opensigweave_cid.lua`. It checks `rspamadm configdump -m` for both enabled
modules and their failure status. For `disabled`, the guard may intentionally
be inactive.

Exit codes: **0** = required files/modules present, **1** = a required
module is missing or disabled, **2** = inspection failed or returned
unrecognized output. Use `--json` for monitoring; exit 2 must be treated
as UNKNOWN, never as success.

Example opt-in root cron entry on the Mailcow host to log failures every
five minutes (adjust paths for your installation):

~~~cron
*/5 * * * * /usr/bin/python3 /opt/OpenSigWeave/scripts/check_mailcow_modules.py --mailcow-dir /opt/mailcow-dockerized --policy enabled --json >/var/log/opensigweave-modules.json || /usr/bin/logger -p daemon.err 'OpenSigWeave Mailcow module health check failed or unavailable'
~~~

The check needs only read-only Compose inspections but Docker daemon
access is highly privileged. Do not mount the Docker socket inside the
OpenSigWeave web application. Run it on a trusted host account.

**Limits:** `rspamadm configdump -m` evaluates configuration, not the precise
state of the currently running worker. Files/module configuration can pass
while signature insertion is broken. The checker cannot reject emails.
After updating Mailcow, Rspamd, or the integration, additionally test an
authenticated SMTP submission and verify exactly one signature in the
received message (plus DKIM where relevant).

A `rspamadm configtest -s` is useful separately, but may fail because of
unrelated Mailcow symbol dependencies (for example
`LOCAL_BL_ASN -> MAILCOW_WHITE`). Do not treat that alone as proof that
OpenSigWeave's modules failed to load.

## Postfix SMTP temporary failures

For `enabled`, set `milter_default_action=tempfail` on the actual
authenticated submission listeners to protect against Rspamd itself
becoming unavailable. Inspect effective settings, not just main.cf:

~~~bash
cd /opt/mailcow-dockerized
python3 /opt/OpenSigWeave/scripts/audit_mailcow_submission.py \
  --postconf-command "docker compose exec -T postfix-mailcow postconf" \
  --expected-milter rspamd
~~~

Do not blindly change inbound port 25. This audit checks Milter wiring,
not signature insertion. In `disabled` mode, an unavailable Rspamd Milter
may still trigger Postfix temporary rejection depending on its own policy.

See [Mailcow/Rspamd integration](MAILCOW_RSPAMD_INTEGRATION.md) and
[acceptance tests](MAIL_USAGE_ACCEPTANCE_MATRIX.md).
