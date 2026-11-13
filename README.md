# OpenSigWeave

OpenSigWeave is a self-hosted application for centrally managing email signatures. It provides a web portal, a signature/rule engine, versioned HTML signature storage and an adapter for Rspamd.

The current preview is a **standalone Docker Compose deployment**. It runs OpenSigWeave and PostgreSQL independently of a mail server. Mailcow is not required to install or test the portal.

> **Preview status:** The Docker image, PostgreSQL migrations, application healthcheck and authenticated engine API smoke test pass in CI. The complete browser workflow and end-to-end mail delivery path have not yet been validated. Do not connect this preview to a production mail flow.

## What is implemented

- FastAPI web application with email-first, domain-routed authentication through managed Authentik OIDC or LDAP / Active Directory over LDAPS.
- Independent per-domain login-provider and profile-attribute-provider assignments; successful profile attributes are cached in PostgreSQL per provider and normalized sender address.
- Group-based portal authorization:
  - `signature-users`: normal portal access.
  - `signature-admins`: administrator access.
- Domain settings, personal overrides and administrator controls.
- Signature catalog with HTML versions stored as files and metadata stored in PostgreSQL.
- Signature variants, weighted selection and rules based on sender, domain, groups and attributes.
- Four engine modes: `AUTO`, `PROFESSIONAL`, `INTERNAL` and `NONE`.
- Explicit signature selection through the message-body marker `<---[Signature name]--[4-character code]--->`; the X-OpenSigWeave-Mode header has priority.
- HTML sanitization, sandboxed previews, CSRF protection, secure session cookies and shared database-backed rate limits.
- Rspamd Lua adapter. Multipart messages are deliberately left unchanged by the current adapter.

## Explicit signature marker

The administrator signature catalog displays a ready-to-copy marker for each active or inactive signature. Its four-character check is the first four hexadecimal characters of the SHA-256 digest of the signature name after Unicode case-folding, encoded as UTF-8. Matching is case-insensitive.

Example format:

```text
<---[Professional]--[a1b2]--->
```

Replace the illustrative code with the code displayed beside the signature in the administrator catalog. The marker selects that exact active signature for the sender's domain; it does not act as authentication or authorization. A valid `X-OpenSigWeave-Mode` header takes precedence. Invalid or duplicate mode headers are ignored and selection continues with the body marker, Outlook marker or automatic mode. A body marker is consumed even when it is invalid, overridden by an X-header, or no signature can be inserted.

The current Rspamd adapter still leaves all top-level multipart messages unchanged. Therefore body-marker replacement currently applies only to non-multipart messages handled by the adapter. Multipart MIME support and full client interoperability remain separate integration work.

## Managed identity providers

Login authentication and employee profile attributes are configured independently. Multiple Authentik and LDAP / Active Directory providers can be created in the administrator portal. Each email domain can select one login provider and a separate profile-attribute provider.

On the first administrator visit, the portal opens identity-provider setup automatically when no provider exists. Provider settings are stored outside PostgreSQL in a private file for each provider:

```text
/app/.idp/
├── authentik-main/
│   └── .env
└── samba-ad/
    └── .env
```

The Docker Compose stack persists this directory in the `opensigweave-idp-data` volume. Provider directories use mode `0700`; configuration files use mode `0600`. Secrets are never returned by the management API or stored in the metadata table. Editing a provider while leaving its password/token field empty preserves the existing secret. Provider names are restricted to safe lowercase slugs to prevent path traversal.

**Authentik provider settings**
- HTTPS OIDC discovery URL, client ID, client secret, scopes and exact HTTPS callback URI.
- Optional HTTPS Authentik API URL and read-only API token for profile-attribute lookup.
- The provider-specific callback URI must end in `/auth/{provider-id}` and must be registered at the identity provider.

**LDAP / Active Directory provider settings**
- LDAPS URL, read-only bind DN and password, Base DN, email search filter, timeout and optional private CA certificate file.
- Login first searches for an exact email match with the service account, then verifies the supplied password using a separate bind as the user's distinguished name.
- Certificate verification is always enabled. Disabled Active Directory accounts and ambiguous email matches are rejected.

**Login and profile selection**
- LDAP users and the local administrator use the email/password form.
- Each configured Authentik provider appears as its own sign-in box labelled `Anmelden mit {display_name}`. The callback verifies that the returned email domain is assigned to the selected provider.
- Authentik uses OIDC Authorization Code; LDAP users enter their directory password.
- New installations default to `OIDC_ENABLED=false`; the previous root-level, single-provider OIDC configuration remains available only as an explicit compatibility mode with `OIDC_ENABLED=true`.
- The selected profile provider independently supplies name, phone, title, groups, claims and custom attributes to the portal and signature engine. If no profile provider is assigned, external profile attributes are empty.

The identity cache is isolated by provider ID and normalized sender address, so two directories cannot overwrite each other's profile data. When a provider is unavailable, the last successful cache entry for that same provider is used.

## Current limitations

- Live interactive authentication and discovery-based logout against real Authentik and Active Directory servers still need an end-to-end test. The legacy global OIDC compatibility path retains its Authentik-specific logout behavior.
- The previous global OIDC configuration is retained for compatibility; new installations should use per-provider `.idp/{provider-id}/.env` files.
- The Rspamd adapter is not automatically installed or configured by the standalone Compose stack.
- Every top-level `multipart/*` message is left unchanged. The current rewrite path is limited to non-multipart messages; do not assume that HTML messages with attachments or multipart/alternative are modified.
- Real-browser testing, Outlook compatibility and Rspamd/DKIM ordering still require isolated integration tests.
- CSP is currently report-only. Do not treat it as an enforced browser security boundary.
- This is a preview, not a production-ready mail-flow release.

---

## 1. Requirements

For the standalone Docker deployment you need:

- A Linux host with Docker Engine.
- Docker Compose plugin v2 (`docker compose`).
- Git.
- A DNS name for the portal, for example `signature.example.org`.
- HTTPS termination through a reverse proxy (the session cookie is Secure).
- A local administrator email and scrypt password hash for emergency access.
- An Authentik OIDC provider or LDAP / Active Directory directory if users should sign in through a managed identity provider.

The application container listens on port `8085`. Compose publishes it on `127.0.0.1:8085` only. PostgreSQL is not published to the host.

## 2. Install the standalone preview

The commands below install the current development preview branch. They do not modify Mailcow or any mail-server configuration.

### 2.1 Create the installation directory and clone the branch

Run as a user who can access Docker:

```bash
sudo mkdir -p /opt/opensigweave
sudo chown "$USER":"$USER" /opt/opensigweave
git clone --branch architecture/refactor-foundation https://github.com/VeiglbergTech/OpenSigWeave.git /opt/opensigweave
cd /opt/opensigweave
```

### 2.2 Create the environment file

```bash
cp .env.example .env
chmod 600 .env
```

Generate three different random secrets. Run this command three times and use a different result for each variable:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Edit the environment file:

```bash
nano .env
```

Replace these values:

| Variable | Purpose |
|---|---|
| `POSTGRES_PASSWORD` | Password for the PostgreSQL database user |
| `SECRET_KEY` | Signs the application session cookie |
| `ENGINE_API_KEY` | Authenticates Rspamd to the signature API |

All three values must be independent. Use the generated URL-safe values without adding whitespace. Do not reuse the same secret for different purposes and never commit `.env` to Git.

The Compose file constructs the internal PostgreSQL `DATABASE_URL` from `POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_DB`. Keep the example `DATABASE_URL` password synchronized if you inspect or use it outside Compose; Compose overrides it for the application container.

### 2.3 Configure the local administrator emergency account

New installations use managed identity providers and keep a local administrator account as an independent recovery path. Set:

```dotenv
OIDC_ENABLED="false"
APP_ALLOWED_HOSTS="signature.example.org"
LOCAL_ADMIN_EMAIL="admin@example.org"
LOCAL_ADMIN_PASSWORD_HASH='scrypt$...'
```

Generate the password hash interactively from the repository root:

```bash
python scripts/hash_password.py
```

The utility prompts twice and prints a salted scrypt hash. Put that complete value in `LOCAL_ADMIN_PASSWORD_HASH`, wrapped in single quotes because the hash contains `#` characters. Never put the plaintext password in the environment file. Use a password of at least 12 characters and protect `.env` with mode `0600`.

With `OIDC_ENABLED=false`, the legacy root-level OIDC client is not initialized and the legacy `/auth` callback is not registered. Managed provider callbacks at `/auth/{provider-id}` remain available. The local account receives the `signature-admins` and `signature-users` roles. Configure a separate profile provider on each domain if signatures need directory-backed profile attributes.

### 2.4 Configure a managed Authentik OIDC provider

Create an OAuth2/OpenID Provider and an application in Authentik. Choose a stable provider ID in OpenSigWeave, for example `authentik-main`. Register this exact HTTPS redirect URI at Authentik:

```text
https://signature.example.org/auth/authentik-main
```

Replace the hostname and provider ID with your actual values. In OpenSigWeave, open **Administration → Identity Providers**, create an Authentik provider and enter:

- **OIDC discovery URL:** for example `https://auth.example.org/application/o/opensigweave/.well-known/openid-configuration`.
- **OIDC client ID** and **OIDC client secret** from the Authentik provider.
- **OIDC scopes:** normally `openid email profile groups` plus any custom scopes you use.
- **OIDC callback / redirect URI:** the exact URI registered above.
- **OIDC post-logout redirect URI:** optional; register it at Authentik if your provider requires an allowlist.
- **Authentik API URL** and a read-only **API token** only if this provider will also be used for employee profile lookup.

The provider's settings are written to `.idp/authentik-main/.env`. They are not stored in the root `.env` or returned by the management API.

The OIDC UserInfo response must contain:
- `sub`: stable user subject.
- `email`: the user's email address.
- `email_verified`: the JSON boolean `true`.
- `groups`: a JSON array of group names.

**Important for Authentik 2025.10 and newer:** the default `email` scope mapping may return `email_verified: false` unless you explicitly provide a verified value. OpenSigWeave rejects login unless the claim is the JSON boolean `true`. Do not simply hard-code it to true for unverified addresses.

If you use a custom scope mapping, return a verified boolean only after the email address has actually been verified. For example:

```python
return {
    "email_verified": request.user.attributes.get("email_verified", False),
}
```

Keep the provider's default group mapping or add a mapping that returns a `groups` array. Create these groups in Authentik:

- `signature-users`
- `signature-admins`

Add ordinary portal users to `signature-users`. Add administrators to `signature-admins`; administrators can also be members of `signature-users` if they need the normal user view.

If Authentik is also selected as the domain's **Profile Provider**, create a dedicated service account and API token:

1. Open **Directory → Users → New User** and select **Service Account**.
2. Create a dedicated role under **Directory → Roles**.
3. Grant that role the global **Can view User** permission. The current attribute provider queries `/api/v3/core/users/` by email and needs read access to user records; it does not need user write permissions.
4. Assign the role to the service account.
5. Open **Directory → Tokens and App passwords → Create**, select the service account and choose **API Token** as the intent.
6. Copy the token when shown and enter it in the provider's API token field in OpenSigWeave.

Do not use a personal administrator token. Prefer an expiring token and document its renewal date. See the [Authentik service account documentation](https://docs.goauthentik.io/users-sources/user/account-types/service-accounts/) and [permission management](https://docs.goauthentik.io/users-sources/access-control/manage_permissions/).

Finally, create or edit the relevant domain in OpenSigWeave and select **Authentik Main** as its Login Provider. Select a profile provider independently; the two assignments do not have to point to the same provider.

Official Authentik references: [create an OAuth2/OIDC provider](https://docs.goauthentik.io/add-secure-apps/providers/oauth2/create-oauth2-provider/), [OAuth2 provider scopes and email verification](https://docs.goauthentik.io/add-secure-apps/providers/oauth2/), and [provider property mappings](https://docs.goauthentik.io/add-secure-apps/providers/property-mappings/).

### 2.5 Configure LDAP / Active Directory

Create a dedicated directory account with read-only access to user objects and group membership. Add an LDAP provider in **Administration → Identity Providers**, for example with the ID `samba-ad`, then enter:

- **LDAPS URL:** `ldaps://dc.example.org:636`.
- **Bind DN** and **Bind password:** the read-only service account credentials.
- **Base DN:** for example `DC=example,DC=org`.
- **User filter:** the default is `(&(objectCategory=person)(objectClass=user)(mail={email}))`. Keep the `{email}` placeholder; the application escapes the supplied address before searching.
- **Timeout:** normally 5 seconds.
- **CA certificate file:** optional. If the directory uses a private CA, place the CA certificate in the provider directory and set its full container path, for example `/app/.idp/samba-ad/ca.pem`.

The LDAP provider always uses TLS certificate verification. Do not use `ldap://`, disable certificate validation or bind with a domain administrator account.

Use **Test connection** to verify the service bind and search. A successful search with no matching test user still confirms that the connection works. To enable login, assign this provider as the domain's **Login Provider**. You may independently assign the same or another provider as **Profile Provider**.

For a successful login, the user's `mail` attribute must exactly match the submitted email address. The user must be enabled in Active Directory and belong to a group whose CN maps to `signature-users` or `signature-admins`. The current provider reads direct `memberOf` memberships; nested group expansion is not implemented.

### 2.6 Review the environment

Always configure these common values in the root `.env`:

```dotenv
POSTGRES_PASSWORD="..."
SECRET_KEY="..."
ENGINE_API_KEY="..."
OIDC_ENABLED="false"
APP_ALLOWED_HOSTS="signature.example.org"
LOCAL_ADMIN_EMAIL="admin@example.org"
LOCAL_ADMIN_PASSWORD_HASH='scrypt$...'
```

The root `.env` contains application and database settings. Managed Authentik and LDAP credentials belong in their respective `.idp/{provider-id}/.env` files, which are created and maintained by the portal.

Set `OIDC_ENABLED="true"` only when you deliberately need the legacy, single-provider root-level OIDC compatibility flow. In that mode, configure the legacy OIDC variables in the root `.env`. It is not required for managed Authentik or LDAP providers.

In managed-provider mode, `APP_ALLOWED_HOSTS` must contain at least one explicit portal hostname. Never use wildcards, schemes, ports or paths in this setting.

### 2.7 Validate and start Docker Compose

From `/opt/opensigweave`:

```bash
docker compose config --quiet
docker compose up -d --build
```

Check the containers:

```bash
docker compose ps
docker compose logs --tail=100 opensigweave
```

The application waits for PostgreSQL to become healthy, applies Alembic migrations, and then starts Uvicorn on port `8085`. The application healthcheck verifies that the web process accepts a local TCP connection.

Useful commands:

```bash
# Follow application logs
docker compose logs -f opensigweave

# Follow PostgreSQL logs
docker compose logs -f postgres

# Restart the application
docker compose restart opensigweave

# Stop the stack without deleting persistent data
docker compose down

# Start it again
docker compose up -d
```

**Do not use `docker compose down --volumes` for normal maintenance.** That command deletes the PostgreSQL and signature-storage volumes and therefore destroys the application data.

## 3. Configure HTTPS reverse proxy

The browser-facing URL must use HTTPS because the application session cookie is Secure and the OIDC redirect URI must be HTTPS.

The following Caddy example assumes Caddy runs on the same host and can reach the host loopback interface:

```caddyfile
signature.example.org {
    reverse_proxy 127.0.0.1:8085
}
```

Caddy obtains and renews the certificate automatically when DNS and inbound connectivity are configured correctly.

If your reverse proxy runs in a separate Docker bridge network or on another host, `127.0.0.1` will not refer to the OpenSigWeave host from inside that proxy. Do not expose port `8085` publicly. Instead, deliberately publish it on a private interface, restrict access with the host firewall to the proxy address/network, and configure `TRUSTED_PROXY_CIDRS` only for the actual proxy peer networks. Never trust arbitrary client-supplied forwarding headers.

The reverse proxy must preserve the canonical Host header. In managed-provider mode the application validates it against `APP_ALLOWED_HOSTS`; the legacy global OIDC compatibility mode also derives its canonical hostname from `OIDC_REDIRECT_URI`.

After HTTPS is active, open:

```text
https://signature.example.org/
```

You should see the login page. Choose an `Anmelden mit ...` box for Authentik, or enter an email address and directory password for LDAP. The local administrator uses the same email/password form.

## 4. First application test

After signing in:

1. Confirm a normal user can access the portal but cannot access administrator functions.
2. Sign in as a member of `signature-admins`.
3. Create a domain in the administration area, for example `example.org`.
4. Enable the domain.
5. Create a signature in the signature catalog.
6. Add a simple test signature, for example:

   ```html
   <table>
     <tr><td>{{ first_name }} {{ last_name }}</td></tr>
     <tr><td>{{ title }}</td></tr>
     <tr><td>{{ phone }}</td></tr>
     <tr><td>{{ email }}</td></tr>
   </table>
   ```

7. Save it and verify that the catalog lists the signature and its initial version/variant.
8. Create or adjust a rule if you want to test group, sender or attribute matching.
9. Use the preview and verify that untrusted HTML is sanitized.

The first Docker CI smoke test exercises the authenticated engine endpoint against PostgreSQL. It does not simulate a real OIDC browser login or a complete SMTP transaction; those remain manual preview tests.

## 5. Persistent data and backups

Compose creates three named volumes:

- `opensigweave-postgres-data`: PostgreSQL database.
- `opensigweave-signatures`: versioned HTML signature files.
- `opensigweave-idp-data`: managed identity-provider credentials under `/app/.idp`.

The host `.env` is not stored in a Docker volume. A complete disaster-recovery backup must include all three volumes, the Compose definition and an encrypted copy of `.env`. Do not delete any volume when upgrading or recreating containers.

### Create a complete encrypted backup

Install Docker Compose v2 and the age command-line utility on the host. Create an age recipient and keep its matching private identity separately (preferably offline or on removable storage). The private identity must be available temporarily to verify a newly created backup.

Run from the host:

```bash
python3 backup_cli.py create \
  --project-dir /opt/opensigweave \
  --output /backup/opensigweave-$(date -u +%Y%m%dT%H%M%SZ).age \
  --recipient age1... \
  --identity /media/offline/age-identity.txt
```

The command validates the Compose project and required volumes, stops only the application service, keeps PostgreSQL running for a native custom-format dump, snapshots signature and IDP files, encrypts `.env` separately, and verifies the finished encrypted archive by decrypting it to a private temporary file. The application service is restarted after success or failure. If restart recovery fails, the command reports this explicitly.

Store the resulting `.age` file off-host. Never store the only backup on the same Docker host. The age private identity is not included in the archive and must be retained separately; without it, backups cannot be restored.

To verify an existing backup later:

```bash
python3 backup_cli.py verify /backup/opensigweave-backup.age \
  --identity /media/offline/age-identity.txt
```

The restore workflow is not implemented yet. Do not treat successful backup verification as proof that a cross-host restore has been tested.

## 6. Update the preview

Before updating, create a consistent backup of both persistent volumes and keep a copy of `.env`.

Then, from the installation directory:

```bash
git fetch origin
git pull --ff-only origin architecture/refactor-foundation
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 opensigweave
```

The container applies pending Alembic migrations before starting the web process. Read the release notes and migration notes before updating a test instance that contains data.

## 7. Optional Rspamd integration (not part of standalone installation)

The standalone Compose stack does not install Rspamd, change Mailcow or enable message rewriting. Keep this step separate until the portal and signature rules have been tested.

The adapter files are:

- `rspamd/opensigweave.lua`
- `rspamd/opensigweave.conf.example`

The example configuration uses the same `ENGINE_API_KEY` as the application and an HTTPS URL ending in `/api/signature/`. Store the actual key only in the protected Rspamd configuration; never put it in the Lua source file.

**Current safety limitation:** the Lua adapter deliberately leaves every top-level `multipart/*` message unchanged. This includes common messages with `multipart/alternative` or attachments. Only non-multipart messages enter the current rewrite path. The actual Rspamd processing order, SMTP sender authorization and DKIM signing order have not yet been validated in a disposable Mailcow environment. Do not enable this adapter in a production mail flow.

If you later test it, use a disposable Mailcow/Rspamd instance, retain Mailcow's sender verification, and verify the final delivered message's DKIM signature after mutation. See [the Rspamd review](docs/WEB_AND_RSPAMD_REVIEW.md).

## 8. Troubleshooting

### Application container keeps restarting

```bash
docker compose ps
docker compose logs --tail=200 opensigweave
```

Check that `SECRET_KEY` and `ENGINE_API_KEY` are independently generated values of at least 32 characters and that no placeholder remains.

### PostgreSQL password validation fails

```bash
docker compose config
```

Check that `POSTGRES_PASSWORD` is set and that the Compose-generated `DATABASE_URL` uses the same value. If you changed PostgreSQL credentials after the database volume was initialized, changing the environment variable alone does not change the password stored inside the existing database. Use a documented PostgreSQL password-rotation procedure; do not delete the volume to work around it.

### Browser reports an invalid Host

Use a hostname listed in `APP_ALLOWED_HOSTS`. In legacy global OIDC mode, the hostname in `OIDC_REDIRECT_URI` is also canonical.

### OIDC callback fails

Check:

- The provider is enabled and its discovery URL is reachable from the container over HTTPS.
- The Client ID and Client Secret match the managed Authentik provider configuration.
- The redirect URI registered in Authentik exactly matches the provider's `OIDC_REDIRECT_URI` and ends in `/auth/{provider-id}`.
- The reverse proxy preserves the canonical Host header.
- UserInfo returns a verified email and a `groups` JSON array containing the expected group names.

### User can log in but has no portal access

Check group mappings in Authentik. The group names must exactly match `signature-users` or `signature-admins`.

### Signature profile fields are empty

Check `AUTHENTIK_URL`, `AUTHENTIK_API_TOKEN`, the service account permissions and the exact active user email in Authentik. Check the application logs for attribute lookup warnings. The current attribute provider fails closed to empty attributes if lookup fails.

### Reverse proxy cannot connect

The Compose port is bound to host loopback. A proxy running in a separate bridge-network container cannot reach the host's `127.0.0.1` through its own loopback. Use a deliberate private-interface binding and firewall rule as described above; do not publish the service to the public internet.

## 9. Documentation and project status

- [Technical architecture](docs/ARCHITECTURE.md)
- [Management and engine API](docs/API.md)
- [Implementation progress and verification](docs/IMPLEMENTATION_PROGRESS.md)
- [Repository audit](docs/REPOSITORY_AUDIT.md)
- [Web and Rspamd security review](docs/WEB_AND_RSPAMD_REVIEW.md)

The current development work is on `architecture/refactor-foundation`. The pull request is a draft and is not a production release. The standalone product is being completed first; a native Mailcow UI/integration is planned as a separate future project.

## License

OpenSigWeave is licensed under the GNU General Public License v3.0. See [LICENSE](LICENSE).


### Signature file layout

Signature HTML is stored by name under `signatur/{Signaturname}/signature.html`. Immutable version snapshots are kept in the same signature directory under `.versions/`; PostgreSQL keeps the stable internal signature ID and version metadata. Renaming a signature renames its directory and updates stored relative paths without changing the internal ID.

