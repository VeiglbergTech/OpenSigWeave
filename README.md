# OpenSigWeave

Self-hosted email-signature management with a web portal, rule and rendering engine, versioned HTML storage, managed identity providers and an optional Rspamd adapter.

**Status:** Development preview, not production-ready. Deploys independently of Mailcow; the standalone Compose stack does not install Rspamd or modify a mail server. Do not connect it to production mail flow.

## Features

- FastAPI portal and management API; PostgreSQL-backed metadata and shared rate limits.
- Per-domain login-provider and profile-provider assignments: Authentik OIDC or LDAP/Active Directory over LDAPS.
- Local administrator emergency login; role groups `signature-users` and `signature-admins`.
- Signature catalog, immutable HTML versions, weighted variants, groups and sender/domain/group/claim/attribute rules.
- Modes: `AUTO`, `PROFESSIONAL`, `INTERNAL`, `NONE`; explicit message-mode header and body marker support.
- Server-side HTML sanitization, sandboxed previews, CSRF protection, secure sessions and trusted-host validation.
- Encrypted full backups, fresh-deployment restore, verified retention and optional systemd scheduling to remote storage.
- Separate Lua adapter for Rspamd; top-level `multipart/*` messages are deliberately left unchanged.

## Requirements

- Linux host, Docker Engine and Docker Compose plugin v2 (`docker compose`).
- Git and a DNS name for the portal.
- HTTPS reverse proxy; the session cookie is Secure and OIDC redirect URIs must use HTTPS.
- Local administrator email and scrypt password hash for emergency access.
- Authentik OIDC or LDAP/Active Directory if users should authenticate through a managed directory.

The application listens on TCP `8085` inside the container. Compose binds it to `APP_BIND_ADDRESS` (default `127.0.0.1`); PostgreSQL is not published to the host. Compose uses `postgres:17-alpine`.

## Quick start

The commands deploy the development preview branch and do not change Mailcow or mail-server configuration.

```bash
sudo mkdir -p /opt/opensigweave
sudo chown "$USER":"$USER" /opt/opensigweave
git clone --branch architecture/refactor-foundation https://github.com/VeiglbergTech/OpenSigWeave.git /opt/opensigweave
cd /opt/opensigweave
cp .env.example .env
chmod 600 .env
```

Generate three independent secrets (run once per variable):

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Edit `.env`:

```bash
nano .env
```

Set at least:

| Variable | Purpose |
|---|---|
| `POSTGRES_PASSWORD` | PostgreSQL password |
| `SECRET_KEY` | Session-cookie signing secret |
| `ENGINE_API_KEY` | Rspamd engine API authentication |
| `OIDC_ENABLED` | `false` for managed providers and local login; `true` only for legacy global OIDC |
| `APP_ALLOWED_HOSTS` | Explicit portal hostnames; required in local/managed-provider mode |
| `LOCAL_ADMIN_EMAIL` | Local emergency administrator |
| `LOCAL_ADMIN_PASSWORD_HASH` | Salted scrypt hash; never store the plaintext password |

Use different random values for all three secrets. Do not commit `.env`. Keep it mode `0600`. Compose builds the internal `DATABASE_URL` from `POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_DB`; the Compose value overrides the example URL in the application container.

Generate the local administrator hash:

```bash
python scripts/hash_password.py
```

Enter the complete output in `LOCAL_ADMIN_PASSWORD_HASH`, enclosed in single quotes. Use a password of at least 12 characters. In managed-provider mode, set `OIDC_ENABLED="false"`; the local administrator receives both `signature-admins` and `signature-users`.

### Environment reference

Defaults are defined in [`.env.example`](.env.example).

| Variable | Default / requirement |
|---|---|
| `POSTGRES_DB` | `opensigweave` |
| `POSTGRES_USER` | `opensigweave` |
| `POSTGRES_PASSWORD` | Required; set a unique random value |
| `DATABASE_URL` | Compose overrides it for the application using the PostgreSQL service |
| `SIGNATURE_STORAGE_PATH` | `/data/signatures` |
| `SECRET_KEY`, `ENGINE_API_KEY` | Required independent random secrets |
| `OIDC_ENABLED` | `false` in the example; `true` enables legacy global OIDC |
| `OIDC_DISCOVERY_URL` | Legacy OIDC discovery URL; only used when enabled |
| `OIDC_REDIRECT_URI` | Legacy OIDC callback URI; only used when enabled |
| `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | Legacy OIDC client credentials |
| `OIDC_SCOPES` | `openid email profile opensigweave` |
| `APP_ALLOWED_HOSTS` | Empty in example; set explicit hostnames, comma-separated, no wildcard/scheme/port/path |
| `LOCAL_ADMIN_EMAIL` | `admin@example.com` |
| `LOCAL_ADMIN_PASSWORD_HASH` | Empty in example; required for local login |
| `LOGIN_RATE_LIMIT` | `10` |
| `AUTH_CALLBACK_RATE_LIMIT` | `20` |
| `ENGINE_RATE_LIMIT` | `1800` |
| `APP_BIND_ADDRESS` | `127.0.0.1`; set to the application host private IP only for a remote proxy |
| `TRUSTED_PROXY_CIDRS` | Empty; set to the exact trusted reverse-proxy source address (`/32` or `/128`) |

Managed provider credentials do **not** belong in the root `.env`. The portal stores them in `/app/.idp/{provider-id}/.env` on the persistent `opensigweave-idp-data` volume. Provider directories use mode `0700`, files mode `0600`; secrets are not returned by the management API. Leaving a password/token field empty during an edit preserves its existing secret.

### Configure managed identity

Login and profile attributes are assigned independently per email domain. A domain can use one provider for login and another for profile lookup.

**Authentik OIDC**

1. Create an OAuth2/OpenID Provider and application.
2. In OpenSigWeave, create a provider under **Administration → Identity Providers** with a stable ID, e.g. `authentik-main`.
3. Register the exact callback URI `https://signature.example.org/auth/authentik-main` at Authentik and enter the HTTPS discovery URL, client ID, client secret, scopes and callback URI in OpenSigWeave.
4. UserInfo must return `sub`, `email`, `email_verified` as JSON boolean `true`, and `groups` as an array. Authentik 2025.10+ may require a custom scope/property mapping to return a genuinely verified email. Do not hard-code verification for unverified addresses.
5. Create groups `signature-users` and `signature-admins`; assign users to the appropriate groups.
6. If using Authentik as the profile provider, create a dedicated service account and API token with only the global **Can view User** permission. Do not use a personal administrator token.

References: [Authentik OIDC provider](https://docs.goauthentik.io/add-secure-apps/providers/oauth2/create-oauth2-provider/), [scopes](https://docs.goauthentik.io/add-secure-apps/providers/oauth2/), [service accounts](https://docs.goauthentik.io/users-sources/user/account-types/service-accounts/) and [permissions](https://docs.goauthentik.io/users-sources/access-control/manage_permissions/).

**LDAP / Active Directory**

1. Create a dedicated read-only directory account and add an LDAP provider under **Administration → Identity Providers**.
2. Configure an LDAPS URL (e.g. `ldaps://dc.example.org:636`), bind DN/password, Base DN (e.g. `DC=example,DC=org`), timeout (default 5 seconds) and, if needed, a private CA certificate path such as `/app/.idp/samba-ad/ca.pem`.
3. Default user filter: `(&(objectCategory=person)(objectClass=user)(mail={email}))`. Keep the `{email}` placeholder.
4. Assign the provider as the domain's Login Provider and optionally Profile Provider.

TLS certificate verification is mandatory; do not use `ldap://`, disable validation or bind as a domain administrator. Login requires an enabled AD account, an exact `mail` match and direct `memberOf` membership mapped to `signature-users` or `signature-admins`; nested group expansion is not implemented. The login flow searches with the service account, then verifies the password with a separate bind as the user's DN.

For managed providers, `APP_ALLOWED_HOSTS` must contain at least one explicit hostname. The legacy root-level OIDC configuration is compatibility-only and requires `OIDC_ENABLED="true"`; its callback is `/auth`. Managed OIDC callbacks use `/auth/{provider-id}`.

### Start and check

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 opensigweave
```

The application waits for healthy PostgreSQL, applies Alembic migrations and starts Uvicorn on port `8085`.

Useful operations:

```bash
docker compose logs -f opensigweave
docker compose logs -f postgres
docker compose restart opensigweave
docker compose down
docker compose up -d
```

**Never use `docker compose down --volumes` for routine maintenance:** it deletes database, signature and identity-provider data.

## HTTPS reverse proxy

Example Caddy configuration when Caddy runs on the same host:

```caddyfile
signature.example.org {
    reverse_proxy 127.0.0.1:8085
}
```

For a **separate reverse-proxy server**, configure `APP_BIND_ADDRESS` in `.env` to the application server private-interface IP (not `0.0.0.0`), recreate the application container, and configure the application host firewall to allow inbound TCP/8085 **only from the reverse-proxy private IP**. Deny all other sources, including the public interface. Set `TRUSTED_PROXY_CIDRS` to the exact proxy source address as `/32` (IPv4) or `/128` (IPv6), not an entire LAN unless every host in it is trusted. The application uses this setting only to resolve client addresses for rate limiting; it does not make the proxy network boundary for you. Preserve the canonical Host header. Never expose port `8085` publicly or trust arbitrary forwarded headers. If proxy and application communicate over an untrusted network, use an authenticated encrypted tunnel or TLS for the upstream connection.

Open `https://signature.example.org/` after HTTPS is configured.

## First-use check

1. Sign in as a normal user and verify administrator functions are inaccessible.
2. Sign in as a member of `signature-admins`.
3. Create and enable a domain (e.g. `example.org`), then create a signature in the catalog.
4. Save and verify its initial version/variant; test a rule if required.
5. Preview the signature and confirm untrusted HTML is sanitized.

Example HTML:

```html
<table>
  <tr><td>{{ first_name }} {{ last_name }}</td></tr>
  <tr><td>{{ title }}</td></tr>
  <tr><td>{{ phone }}</td></tr>
  <tr><td>{{ email }}</td></tr>
</table>
```

## Signature selection and engine API

The Rspamd engine endpoint is `GET /api/signature/{email}`; it requires `X-Engine-Key` matching `ENGINE_API_KEY`. See [API reference](docs/API.md) for management endpoints, permissions, payloads and selection precedence.

Supported modes: `AUTO`, `PROFESSIONAL`, `INTERNAL`, `NONE`. A valid `X-OpenSigWeave-Mode` header takes precedence over the body marker. The explicit marker format is:

```text
<---[Signature name]--[4-character code]--->
```

The four-character code is the first four hexadecimal characters of the SHA-256 digest of the signature name after Unicode case-folding and UTF-8 encoding. Matching is case-insensitive. Copy the actual code from the administrator catalog; the marker is selection syntax, not authentication or authorization. Invalid/duplicate mode headers are ignored. A body marker is consumed even if invalid, overridden or no signature is inserted.

**MIME limitation:** the current Rspamd adapter leaves every top-level `multipart/*` message unchanged, including `multipart/alternative` and messages with attachments. Body-marker rewriting currently applies only to non-multipart messages.

## Persistence and backups

Compose creates three named volumes:

| Volume | Contents |
|---|---|
| `opensigweave-postgres-data` | PostgreSQL database |
| `opensigweave-signatures` | Current and immutable versioned signature HTML |
| `opensigweave-idp-data` | Managed identity-provider settings and secrets |

Signature files are stored at `signatur/{Signaturname}/signature.html`; immutable snapshots are in the same directory under `.versions/`. PostgreSQL stores stable internal IDs and metadata. Renaming a signature renames its directory without changing its ID.

A complete recovery set includes all three volumes, `compose.yaml` and an encrypted copy of the host `.env`. The age private identity is separate and must be kept offline/off-host; without it, backups cannot be restored. Never keep the only backup on the Docker host.

Create and verify an encrypted backup (requires Docker Compose v2, `age`, an age recipient and its matching private identity):

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

The create operation stops only the application service, dumps PostgreSQL, snapshots signature and IDP volumes, saves exact container images, encrypts `.env` and verifies the resulting archive. It restarts the application after success or failure and reports restart-recovery errors.

**Scheduled off-host backup:** the repository includes a systemd service/timer for a mounted remote filesystem. Configure `/mnt/opensigweave-backups`, create `/etc/opensigweave/backup.env` from `scripts/backup.env.example`, install `scripts/systemd/opensigweave-backup.service` and `scripts/systemd/opensigweave-backup.timer`, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now opensigweave-backup.timer
sudo systemctl start opensigweave-backup.service
sudo systemctl status opensigweave-backup.service
```

The service refuses an absent mount or a filesystem type outside its allowlist (NFS, SMB/CIFS or supported remote FUSE), prevents overlapping runs and verifies archives before retention deletion. The age identity must be root-owned, mode `0600`, outside the share and separately backed up offline. Inspect the first manual run. Retention defaults to dry-run; add `--apply` only to delete older verified archives:

```bash
python3 backup_cli.py prune --directory /mnt/opensigweave-backups --identity /etc/opensigweave/age-identity.txt --keep 14
```

Restore only to a fresh host/directory and unique Compose project name:

```bash
python3 backup_cli.py restore /backup/opensigweave-backup.age \
  --identity /media/offline/age-identity.txt \
  --target-dir /opt/opensigweave-restored \
  --project-name opensigweave-dr
```

Restore refuses existing target directories/volumes and retains newly created resources on failure for inspection. It restores the exact saved images, not a new build. It does not change DNS or reverse-proxy routing. A real two-host restore drill and real NFS/SMB host test remain outstanding; see [backup and disaster recovery](docs/BACKUP_RESTORE_DESIGN.md) and [cross-host restore drill](docs/CROSS_HOST_RESTORE_DRILL.md).

## Optional Rspamd integration

Not included in standalone installation. Adapter files: `rspamd/opensigweave.lua` and `rspamd/opensigweave.conf.example`. Configure the HTTPS engine URL ending in `/api/signature/` and the same `ENGINE_API_KEY` in protected Rspamd configuration; never put the key in Lua source.

Keep Mailcow/Postfix sender verification enabled: an authenticated SMTP user does not by itself prove authorization to use the envelope sender. Verify Rspamd processing order and final DKIM after mutation in a disposable Mailcow environment. No production mail-flow, sender-authorization or DKIM-order test has passed. See [web and Rspamd security review](docs/WEB_AND_RSPAMD_REVIEW.md).

## Troubleshooting

| Symptom | Checks |
|---|---|
| Application restarts | `docker compose ps`; `docker compose logs --tail=200 opensigweave`; check independent `SECRET_KEY` and `ENGINE_API_KEY` values (at least 32 characters) and remove placeholders. |
| PostgreSQL password error | Check `docker compose config` and matching `POSTGRES_PASSWORD`/database URL. Changing the environment variable does not rotate credentials in an initialized database; use a PostgreSQL password-rotation procedure, never delete the volume. |
| Invalid Host | Use a hostname in `APP_ALLOWED_HOSTS`; legacy OIDC also uses the hostname in `OIDC_REDIRECT_URI`. |
| OIDC callback failure | Check provider enabled, HTTPS discovery reachability, client credentials, exact registered callback, canonical Host, verified email and groups array. |
| Login succeeds but no portal access | Check exact `signature-users` / `signature-admins` group mapping. |
| Empty profile fields | Check profile-provider assignment, Authentik URL/token and read permission, or LDAP service-bind access; inspect application logs. Failed lookup returns empty attributes unless a same-provider cache entry is available. |
| Proxy cannot connect | Check `APP_BIND_ADDRESS`, host firewall allowance from the proxy IP, routing/tunnel and TCP/8085. Do not solve this by exposing the port publicly. |

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Management and engine API](docs/API.md)
- [Implementation progress and verification](docs/IMPLEMENTATION_PROGRESS.md)
- [Repository audit](docs/REPOSITORY_AUDIT.md)
- [Web and Rspamd security review](docs/WEB_AND_RSPAMD_REVIEW.md)
- [Backup and disaster recovery](docs/BACKUP_RESTORE_DESIGN.md)
- [Cross-host restore drill](docs/CROSS_HOST_RESTORE_DRILL.md)
- [Rich-text editor decision](docs/EDITOR_DECISION.md)
- [Signature groups design](docs/SIGNATURE_GROUPS_DESIGN.md)

Development is on `architecture/refactor-foundation`; the pull request remains a draft, not a production release. Native Mailcow UI/integration is a separate future project.

## License

[GNU General Public License v3.0](LICENSE).
