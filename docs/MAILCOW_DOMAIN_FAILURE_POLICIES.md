# Mailcow/Rspamd: offline domain-specific signature requirements

**Scope:** Authenticated outbound submissions processed by the installed
OpenSigWeave Rspamd adapter. This is specifically about **OpenSigWeave
API/processing outages**, not complete Rspamd downtime or broken Milter wiring.
No new service, permanent agent or extra Docker container is installed.

## Domain setting

In **Admin → Domains → Manage Domain → General Settings**, set
**Signature required** (default **Off** for new or migrated domains).

| Domain setting | Normal engine availability | Engine timeout, unavailable, auth/5xx or malformed response |
| --- | --- | --- |
| **On** | Apply existing signature rules and rotation | Rspamd soft reject (temporary SMTP failure) |
| **Off** | Apply existing signature rules and rotation | Permit unsigned mail **only with a verified, current local exemption** |
| Unknown domain / missing, stale or invalid local policy | Normal behavior remains subject to existing rules | Soft reject in opted-in policy mode |

The domain is selected using the trusted **authenticated SMTP envelope
sender**, not the potentially forged message `From:` header. Names are
normalized to lower-case; domain matching is exact with no parent-domain
wildcards and no implied subdomain exemption. No per-user exceptions.

An intentional rule result that selects no signature is different from a
technical API failure. The requirement concerns technical failures; normal
rule selection remains unchanged.

## Before enabling the policy

1. Install the standard Mailcow/Rspamd adapter files on the **Mailcow host**,
   and additionally install `rspamd/opensigweave_domain_policy.lua` under
   `/opt/mailcow-dockerized/data/conf/rspamd/lua/` along with
   `opensigweave.lua`, `opensigweave_mime.lua`,
   `opensigweave_cid.lua` and the optional guard. Avoid modifying Mailcow's
   bundled `rspamd.local.lua`.
2. Configure `data/conf/rspamd/rspamd.conf.local` with the existing engine
   HTTPS URL, key, global Failure Policy and the **absolute path visible from
   inside the Rspamd container**:

   ```ucl
   opensigweave {
       api_url = "https://signature.example.org/api/v1/engine/signatures/";
       engine_api_key = "<your-existing-engine-key>";
       failure_policy = "enabled";
       domain_policy_file = "/etc/rspamd/local.d/opensigweave-domain-policy.json";
   }
   opensigweave_guard {
       enabled = true;
   }
   ```

3. Generate an authenticated policy snapshot with
   `GET https://signature.example.org/api/v1/mail/domain-policy` and
   `X-Engine-Key`, or **Admin → Domains → Download mailserver policy**.
   Copy it onto the **Mailcow host** as
   `/opt/mailcow-dockerized/data/conf/rspamd/local.d/opensigweave-domain-policy.json`
   using an atomic replace. Do not use partial copies; protect the source
   and destination permissions against untrusted modification.
4. Validate the JSON file and module configuration, reload/restart Rspamd
   safely and test authenticated submission using a disposable Mailcow.
   **Changing a toggle does not automatically synchronize an existing
   mailserver snapshot**.

### Optional automatic synchronization (no daemon)

Run on the **Mailcow host** as root using an installed copy of
`scripts/sync_domain_policy.py` from the approved matching integration
revision. No Docker socket or OpenSigWeave container is required.
Create a protected file containing only the existing Engine API key:

```bash
install -m 600 /dev/null /root/.opensigweave-engine-api-key
# Populate this file using your secure secret manager, not a literal command argument.
python3 /opt/opensigweave-mail-adapter/sync_domain_policy.py \
  --url https://signature.example.org/api/v1/mail/domain-policy \
  --key-file /root/.opensigweave-engine-api-key \
  --output /opt/mailcow-dockerized/data/conf/rspamd/local.d/opensigweave-domain-policy.json \
  --dry-run
python3 /opt/opensigweave-mail-adapter/sync_domain_policy.py \
  --url https://signature.example.org/api/v1/mail/domain-policy \
  --key-file /root/.opensigweave-engine-api-key \
  --output /opt/mailcow-dockerized/data/conf/rspamd/local.d/opensigweave-domain-policy.json
```

For continuous synchronization add a **deliberate operator-managed root cron**
entry (e.g. every five minutes):

```cron
*/5 * * * * /usr/bin/python3 /opt/opensigweave-mail-adapter/sync_domain_policy.py --url https://signature.example.org/api/v1/mail/domain-policy --key-file /root/.opensigweave-engine-api-key --output /opt/mailcow-dockerized/data/conf/rspamd/local.d/opensigweave-domain-policy.json
```

The helper enforces HTTPS/TLS validation, disallows redirects, authenticates
with a protected key file, bounds download size, checks all domain values and
the expiry window, and uses an atomic file replacement with `fsync`. Failed
syncs leave the **last-known-good file** in place. Rspamd reads and validates
the policy when a technical failure occurs; a restart or reload does not
require contacting OpenSigWeave to load an existing valid snapshot.

**Expiration and revocation:** Snapshots are valid for at most 24 hours.
After that, *all* authenticated technical failures are temporarily rejected
until a new valid snapshot is installed. Disabling required signatures only
takes effect offline after the new policy reaches the mailserver. Conversely,
if an administrator turns a domain from **Off** to **On** while the API is down
or sync is failing, an *older, still-valid* `Off` snapshot can continue
allowing unsigned mail until it expires. This is an unavoidable bounded
staleness window in this agentless, asynchronously synchronized model.
Require synchronous policy propagation/acknowledgment or a mailserver-side
strict override for immediate emergency enforcement; this implementation
does not claim instant atomic cross-host revocation.

## Existing global Failure Policy

When `domain_policy_file` is **omitted**, the existing
`failure_policy = "enabled"` (reject technical failure) or
`"disabled"` (allow) behavior is unchanged; legacy `tempfail` /
`standard` / `fail_open` aliases remain recognized.

When **domain_policy_file is set**, the local domain policy takes precedence
for OpenSigWeave technical failures:
- Explicit, valid **Off** exemption → allow unsigned regardless of global
  `enabled`.
- **On**, unknown or no trusted snapshot → soft reject regardless of global
  `disabled`.

Neither the Rspamd injector nor the optional guard can enforce mail rejection
if **both modules are absent, Rspamd itself is down, or the mailserver bypasses
the Rspamd Milter**. The guard checks missing injector registration only when
the guard is itself installed and enabled. Do not claim independent
enforcement or silently modify Postfix `milter_default_action` for inbound
mail. See [Failure Policies](MAILCOW_FAILURE_POLICIES.md) for audit tooling.

## Same-host and remote-host deployment

The Rspamd plugin must reach the HTTPS engine URL from its own container.
For same-host deployments this can be a private reverse proxy even if the
OpenSigWeave and Mailcow Compose stacks have different networks. For remote
deployments use a properly authenticated HTTPS route restricted to the
mailserver. **Local JSON policy always lives on the Mailcow host**, regardless
of where OpenSigWeave itself runs; only policy download/sync crosses hosts.
Do not expose a public Rspamd controller or install an extra monitoring agent.

## Validation checklist

Before production use verify successful normal signature insertion for both
types of domains, SMTP temporary rejection for required domains under API
outage / timeout / invalid response, unsigned acceptance for verified Off
domains, unknown domain strictness, restart during outage, and real SMTP
`4xx` outcomes. Cover exact subdomains, policy expiration, malformed JSON,
failed syncs and attachment preservation. Lua unit/mocked-adapter tests are
**not** proof of full Mailcow/Milter SMTP behavior; an isolated SMTP end-to-end
test is required. Never perform destructive outage tests on production Mailcow.
