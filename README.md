<p align="center">
  <img src="branding/logo.png" alt="OpenSigWeave" width="520">
</p>

# OpenSigWeave

OpenSigWeave is a self-hosted email-signature management service with a FastAPI administration portal, PostgreSQL-backed rule and rendering engine, versioned signature storage, managed identity providers, scheduling, branding, backups and mail-server adapters.

> **Release status:** beta/release-candidate. The application and repository are heavily CI-tested, but the mail-server adapters are not all production-certified. In particular, live DKIM-chain and real-client acceptance remain separate deployment checks.

<a href="https://www.buymeacoffee.com/veiglbergtech">
  <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me a Coffee" height="60">
</a>

## Branches and deployment policy

| Branch | Purpose |
|---|---|
| `dev` | Active development. Never use this branch as the normal deployment source. |
| `beta` | Deployment branch. It is updated only by fast-forwarding a fully green `dev` commit and is the branch to deploy for beta/release-candidate installations. |
| `main` | Release branch. It is not automatically updated from `beta`. |

CI history: [dev workflow runs](https://github.com/VeiglbergTech/OpenSigWeave/actions?query=branch%3Adev) · [beta workflow runs](https://github.com/VeiglbergTech/OpenSigWeave/actions?query=branch%3Abeta)

A green application workflow means the tested commit passed secret scanning, dependency auditing, static security checks, Python/JavaScript syntax checks, PostgreSQL and SQLite migrations, Docker startup, browser administration workflows, CSP observation, encrypted backup/fresh restore and regression tests. Dedicated Postfix compatibility CI additionally exercises the real PyMilter adapter in disposable Postfix. These checks do **not** replace live identity-provider, DKIM-chain, production-mail-flow, load or real email-client acceptance.

## Features

- FastAPI web portal with administrator and user views.
- PostgreSQL metadata with Alembic migrations.
- Local emergency administrator login plus managed Authentik/generic OIDC and LDAP/Active Directory providers.
- Signature catalog with stable IDs, immutable HTML versions, domain scopes and weighted variants.
- Reusable signature groups with weighted no-repeat selection.
- Rules based on sender, domain, groups, claims and profile attributes.
- Explicit per-message mode/marker selection.
- Date/time scheduling for one signature or signature group, optionally scoped to a domain and resolved by priority.
- CKEditor 5 administration/editor experience plus raw-HTML editing.
- Persistent branding for application name, accent color, logo and favicon.
- Source-preserving HTML storage with sanitization at delivery/preview boundaries.
- Reply-aware placement for supported adapters.
- Encrypted disaster-recovery backup, verification, fresh restore and guarded off-host scheduling.
- Rspamd adapter with parser-backed multipart rewriting plus experimental Postfix Milter and Stalwart MTA-hook adapters.
- Selective, validated administrator ZIP export/import for portable non-secret configuration.

## Adapter status

| Adapter | Current automated evidence | Important remaining acceptance |
|---|---|---|
| Rspamd | Lua syntax, nested multipart/alternative + attachment MIME fixture and contract tests | Parser-backed rewriting for text/plain and text/html while keeping attachments intact; signed/encrypted/embedded MIME skipped. Live Mailcow sender authorization, filter order and DKIM acceptance still required. |
| Postfix Milter | PyMilter runtime, native protocol exchange and real disposable Postfix delivery through the actual adapter with a mock OpenSigWeave engine | Live HTTPS engine credentials/trust, production filter ordering and final DKIM still required. |
| Stalwart | Shared-engine and MIME regression coverage, including multipart, reply placement, attachments and signed/encrypted fail-safe behavior | Live Stalwart SMTP/DKIM end-to-end acceptance still required. |

See [adapter documentation](docs/ADAPTERS.md), [Mailcow/Rspamd staging integration](docs/MAILCOW_RSPAMD_INTEGRATION.md), [Postfix design](docs/POSTFIX_MILTER_DESIGN.md) and [web/Rspamd security review](docs/WEB_AND_RSPAMD_REVIEW.md).

## Requirements

- Linux host
- Docker Engine
- Docker Compose plugin v2
- Git
- HTTPS reverse proxy and DNS name for the portal
- PostgreSQL is supplied by the Compose stack
- For full backups: `age` and a separate age private identity
- Optional external identity provider if users should sign in with OIDC or LDAP/Active Directory

The application listens on TCP `8085` inside the container. Compose binds it to `APP_BIND_ADDRESS`, which defaults to `127.0.0.1`. PostgreSQL is not published to the host.

## Install from beta

Clone the deployment branch:

```bash
sudo mkdir -p /opt/opensigweave
sudo chown "$USER":"$USER" /opt/opensigweave
git clone --branch beta --single-branch https://github.com/VeiglbergTech/OpenSigWeave.git /opt/opensigweave
cd /opt/opensigweave
cp .env.example .env
```

Generate independent random values for `POSTGRES_PASSWORD`, `SECRET_KEY` and `ENGINE_API_KEY`. Do not reuse one secret for another:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Generate the local emergency administrator password hash:

```bash
python scripts/hash_password.py
```

Edit `.env` and set at least:

- `POSTGRES_PASSWORD`
- matching `DATABASE_URL` password
- `SECRET_KEY` — at least 32 non-placeholder characters
- `ENGINE_API_KEY` — separate value, at least 32 non-placeholder characters
- `APP_ALLOWED_HOSTS` — explicit portal hostname(s), no wildcard
- `LOCAL_ADMIN_EMAIL`
- `LOCAL_ADMIN_PASSWORD_HASH`
- `APP_BIND_ADDRESS` if the reverse proxy is not on the same host

Validate and start:

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 opensigweave
```

Do not expose PostgreSQL. If the reverse proxy is remote, set `APP_BIND_ADDRESS` to the application server's **private-interface IP** and restrict TCP/8085 in the host firewall **only from the reverse-proxy private IP**.

## Reverse proxy and HTTPS

OpenSigWeave expects HTTPS in normal operation. The default session cookie is Secure, HttpOnly and SameSite=Lax. Keep the canonical external hostname in `APP_ALLOWED_HOSTS`.

If the immediate reverse proxy is trusted and client-IP-aware rate limiting should use forwarded addresses, set `TRUSTED_PROXY_CIDRS` only to the actual proxy networks. Forwarded headers from untrusted peers are ignored. `TRUSTED_PROXY_CIDRS` changes client-IP attribution for rate limiting; it **does not make the proxy network boundary for you**. The host firewall remains mandatory for a remote proxy.

Do not disable certificate verification for OIDC, LDAP or adapter-to-engine HTTPS connections.

### Signature images and publicly accessible asset URLs

The administrator's **Signatures** CKEditor accepts PNG, JPEG and WebP files via
file-manager drag & drop, image clipboard paste and the toolbar upload control
(GIF remains supported for compatibility). Files up to **2 MiB** are stored
under immutable SHA-256 names in the persistent `opensigweave-signatures`
volume. Signature HTML references an absolute URL like
`https://signature.example.org/signature-assets/<hash>.png`, not a temporary
`blob:` URL or Base64 `data:` URL. Reopening signatures and selecting older
versions does not change these asset URLs; deleting an image reference does
not delete the file because other signatures/versions may use it.

**Deployment requirement:** The public HTTPS hostname used to open the editor
must also be reachable by mail recipients. Allow unauthenticated **GET** requests
to `/signature-assets/<hash>.(png|jpg|webp|gif)` through the reverse proxy,
even when the administration portal is behind SSO or proxy authentication.
Keep all other administrator APIs protected; uploads to
`POST /api/admin/signature-assets` require an administrator session and CSRF
token. The application itself serves only validated, immutable image names.
If the hostname is private-network-only or external-image loading is disabled
by a recipient's mail client, images cannot appear there until the recipient
permits remote images. Include the signature volume in backups.

## First sign-in and identity setup

1. Sign in with the local emergency administrator configured in `.env`.
2. Open **Administration → Identity Providers**.
3. Create the required Authentik/generic OIDC or LDAP/Active Directory provider.
4. Test the provider connection.
5. Open **Domains**, register each mail domain and assign its login provider and profile provider.
6. Map directory users to the exact application groups:
   - `signature-users`
   - `signature-admins`
7. Verify a normal user cannot access administrator functions.

OIDC callback URIs use:

```text
https://<portal-host>/auth/<provider-id>
```

Managed provider credentials do **not** belong in the root `.env`. The portal stores them under `/app/.idp/{provider-id}/.env` on the persistent `opensigweave-idp-data` volume. Provider directories/files use restrictive permissions and secret values are not returned by the management API. Leaving a secret field empty while editing preserves the existing value.

### Authentik / OpenID Connect

For Authentik:

1. Create an OAuth2/OpenID Provider and application.
2. Create the provider in **Administration → Identity Providers** with a stable ID such as `authentik-main`.
3. Register the exact callback URI (for example `https://signature.example.org/auth/authentik-main`) and configure the HTTPS discovery URL, client ID, client secret, scopes and callback URI in OpenSigWeave.
4. UserInfo must provide a stable `sub`, syntactically valid `email`, `email_verified` as JSON boolean `true`, and `groups` as an array. Do not fake email verification for an unverified address.
5. Assign users to `signature-users` and, where appropriate, `signature-admins`.
6. If Authentik is also the profile provider, use a dedicated service account/API token with only the read permission needed for user lookup; do not use a personal administrator token.

Generic OIDC uses the same managed callback model but relies on verified callback/UserInfo claims for portable profile data.

### LDAP / Active Directory

1. Create a dedicated read-only directory service account.
2. Add an LDAP provider under **Administration → Identity Providers**.
3. Configure an LDAPS URL such as `ldaps://dc.example.org:636`, bind DN/password, Base DN, timeout and, if required, the private CA certificate path.
4. The default Active Directory user filter is `(&(objectCategory=person)(objectClass=user)(mail={email}))`; retain the `{email}` placeholder.
5. Assign the provider as the domain's Login Provider and, if desired, Profile Provider.

TLS certificate verification is mandatory. Do not use `ldap://`, disable certificate validation or bind as a domain administrator. Login requires an enabled account, one exact `mail` match and direct `memberOf` membership mapped to the application groups. Nested group expansion is not implemented. Password verification is performed with a separate bind as the user's DN after the service-account lookup.

## Signatures, groups and rules

Administration provides:

- **Signatures** — create and update signatures, immutable versions and domain scopes.
- **Signature Groups** — reusable sets of signatures with weighted no-repeat selection.
- **Rules** — choose an exact signature, a signature group or no signature based on sender/domain/group/claim/attribute conditions.
- **Scheduling** — temporarily override normal selection with one signature or group during a concrete date/time range.
- **Branding** — application name, accent color, logo and favicon.

Signature assets use stable database IDs. On disk, signature HTML is stored under:

```text
signatur/{Signaturname}/signature.html
```

Immutable snapshots live in the same signature directory under `.versions/`. Renaming a signature changes its directory name but not its internal ID.

### Scheduling precedence

Resolution order is:

1. administrator-disabled user override → no signature
2. explicit per-message selection
3. highest-priority active schedule
4. allowed personal override
5. highest-priority matching rule
6. domain template fallback when no rule matches
7. a matching `NONE` rule → no signature

Schedule windows are stored in UTC and evaluated as `starts_at <= now < ends_at`. The browser converts local date/time input to UTC. At equal priority, a domain-specific schedule wins over an all-domain schedule.

The current scheduler intentionally supports concrete ranges only. Recurring annual/weekly schedules are not part of the beta contract.

## Branding

The bundled OpenSigWeave logo is the default. In **Administration → Branding** an administrator can configure:

- application name
- six-digit hex accent color
- PNG logo, maximum 512 KiB
- PNG/ICO favicon, maximum 256 KiB

Uploaded branding is stored in PostgreSQL. Server-side validation checks allowed media type, base64 encoding, size and file signature. Resetting an asset falls back to the bundled branding.

## Update an existing beta deployment

Take and verify a backup before upgrading. Then fast-forward the deployment checkout:

```bash
cd /opt/opensigweave
git fetch origin
git checkout beta
git pull --ff-only origin beta
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 opensigweave
```

The container runs `alembic upgrade head` before starting Uvicorn. Never delete the PostgreSQL volume as an upgrade method.

## Backups and restore

Compose uses three named volumes:

| Volume | Contents |
|---|---|
| `opensigweave-postgres-data` | PostgreSQL data, including rules, schedules and persistent branding |
| `opensigweave-signatures` | canonical and immutable versioned signature HTML |
| `opensigweave-idp-data` | managed provider configuration and credentials |

A complete disaster-recovery set also includes the deployment files and encrypted `.env`. The age private identity is **not** stored in the backup and must be protected separately/off-host.

Create and verify:

```bash
cd /opt/opensigweave
python3 backup_cli.py create \
  --project-dir /opt/opensigweave \
  --output /backup/opensigweave-$(date -u +%Y%m%dT%H%M%SZ).age \
  --recipient age1... \
  --identity /media/offline/age-identity.txt

python3 backup_cli.py verify /backup/opensigweave-backup.age \
  --identity /media/offline/age-identity.txt
```

Restore only into a fresh target:

```bash
python3 backup_cli.py restore /backup/opensigweave-backup.age \
  --identity /media/offline/age-identity.txt \
  --target-dir /opt/opensigweave-restored \
  --project-name opensigweave-dr
```

For scheduled off-host backups, copy `scripts/backup.env.example` to `/etc/opensigweave/backup.env`, install the included systemd service/timer and use a dedicated mounted remote filesystem. The service refuses missing/local targets and unsupported filesystem types.

Full details: [backup/restore design](docs/BACKUP_RESTORE_DESIGN.md) and [cross-host restore drill](docs/CROSS_HOST_RESTORE_DRILL.md).

### Component export is not a backup

The web UI's selective ZIP export currently supports:

- signatures and immutable HTML versions
- domains
- signature groups and memberships
- rules and conditions
- identity-provider metadata

It deliberately excludes provider credentials, application secrets, personal overrides and identity caches. Branding and schedules are currently covered by the full PostgreSQL disaster-recovery backup, not by the selective component export.

Imports are additive and validated; existing records are not silently overwritten.

## Explicit message selection

Adapters may select a concrete signature with the body marker supported by the mail integration:

```text
<---[Signature name]--[4-character code]--->
```

The four-character code is the first four hexadecimal characters of SHA-256 over the Unicode-case-folded UTF-8 signature name. Copy the code shown in the administrator catalog. Mode-based selectors are retired. Mail adapters strip the deprecated selector header and legacy HTML selector markers when encountered, but they no longer influence signature selection.

## Security model

Important enforced properties include:

- no built-in production secret fallbacks
- minimum-length/non-placeholder secret validation
- CSRF protection for state-changing browser requests
- Secure/HttpOnly/SameSite session cookies
- explicit trusted hosts
- database-backed rate limiting
- bounded request bodies and adapter responses
- HTML sanitization before preview/delivery
- autoescaped template values
- sandboxed preview iframes
- LDAPS/HTTPS certificate verification
- non-root read-only application container, dropped Linux capabilities and `no-new-privileges`
- PostgreSQL not published by Compose
- secret scanning of complete Git history
- dependency auditing and Bandit analysis in CI

The browser CSP is currently **Report-Only**. Automated Chromium workflows record no CSP violations across the exercised administration/editor flows, but enforcement remains a separate acceptance step to avoid breaking untested browser/deployment combinations.

See [security review](docs/WEB_AND_RSPAMD_REVIEW.md) and [repository audit](docs/REPOSITORY_AUDIT.md).

## Known acceptance gaps

A green beta build is not equivalent to production mail-flow certification. Before production use, validate what applies to your environment:

- live OIDC/LDAP sign-in and group mapping
- real reverse-proxy forwarded-header topology
- actual sender authorization at the MTA
- adapter/filter ordering
- final DKIM after OpenSigWeave mutation
- SOGo, Outlook and Thunderbird behavior with your real signatures
- cross-host restore on independent infrastructure
- real NFS/SMB disconnect/recovery
- throughput/load and failure-policy behavior

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [API](docs/API.md)
- [Engine service](docs/ENGINE_SERVICE.md)
- [Adapters](docs/ADAPTERS.md)
- [Postfix Milter design](docs/POSTFIX_MILTER_DESIGN.md)
- [Signature groups](docs/SIGNATURE_GROUPS_DESIGN.md)
- [Client compatibility](docs/CLIENT_COMPATIBILITY.md)
- [Editor decision](docs/EDITOR_DECISION.md)
- [HTML source preservation](docs/HTML_SOURCE_PRESERVATION_ADR.md)
- [Backup/restore design](docs/BACKUP_RESTORE_DESIGN.md)
- [Cross-host restore drill](docs/CROSS_HOST_RESTORE_DRILL.md)
- [Security review](docs/WEB_AND_RSPAMD_REVIEW.md)
- [Implementation status](docs/IMPLEMENTATION_PROGRESS.md)
- [Repository audit](docs/REPOSITORY_AUDIT.md)

## License

OpenSigWeave is distributed under the [GNU General Public License v3](LICENSE). Third-party components keep their own licenses; see [third-party notices](static/vendor/THIRD_PARTY_NOTICES.md).
