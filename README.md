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
- The login page asks for the email address first. The email domain selects the configured login provider.
- Authentik uses OIDC Authorization Code; LDAP users enter their directory password.
- The local administrator can sign in through the same form as an independent emergency path.
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
- A local administrator email and scrypt password hash for emergency access. Configure Authentik OIDC or LDAP / Active Directory providers in the portal; root-level legacy OIDC is optional (`OIDC_ENABLED=true`).

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

### 2.3 Run without OIDC (local administrator)

For a standalone installation using managed providers, set:

```dotenv
OIDC_ENABLED="false"
APP_ALLOWED_HOSTS="signature-test.example.org"
LOCAL_ADMIN_EMAIL="admin@example.org"
LOCAL_ADMIN_PASSWORD_HASH='scrypt$...'
```

Generate the password hash interactively from the repository root:

```bash
python scripts/hash_password.py
```

The utility prompts twice and prints a salted scrypt hash. Put that complete value in `LOCAL_ADMIN_PASSWORD_HASH`, wrapped in single quotes because the hash contains `# OpenSigWeave

OpenSigWeave is a self-hosted application for centrally managing email signatures. It provides a web portal, a signature/rule engine, versioned HTML signature storage and an adapter for Rspamd.

The current preview is a **standalone Docker Compose deployment**. It runs OpenSigWeave and PostgreSQL independently of a mail server. Mailcow is not required to install or test the portal.

> **Preview status:** The Docker image, PostgreSQL migrations, application healthcheck and authenticated engine API smoke test pass in CI. The complete browser workflow and end-to-end mail delivery path have not yet been validated. Do not connect this preview to a production mail flow.

## What is implemented

- FastAPI web application with an OIDC login flow.
- Authentik as the currently supported employee-attribute provider; the last successful identity attributes are cached in PostgreSQL per normalized sender address and used when a fresh lookup fails.
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

## Identity attribute fallback

For each sender, OpenSigWeave requests fresh profile attributes from Authentik on every signature lookup. A successful response, including a valid response with empty attributes, replaces that sender's cached record in PostgreSQL. If Authentik is unavailable or the lookup cannot be trusted (for example, an invalid response or ambiguous account match), the last successfully stored record is used. A successful lookup confirming that the account is missing or inactive clears the cached attributes instead of reusing stale group membership. If no successful record exists yet, the engine proceeds with empty profile attributes. The cache includes names, title, phone, groups, claims and custom attributes used by signature rules.

## Current limitations

- OIDC is enabled by default for backward compatibility. Standalone local-administrator login is available with `OIDC_ENABLED=false`; in that mode profile attributes are empty unless populated by a future identity provider.
- Although OIDC discovery is configurable, employee attributes and logout still use Authentik-specific endpoints. A generic OIDC-only deployment is not yet a complete supported setup.
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
- Either an Authentik OAuth2/OpenID Provider (`OIDC_ENABLED=true`, the default), or a local administrator email and scrypt password hash (`OIDC_ENABLED=false`).

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

### 2.3 Run without OIDC (local administrator)

For an isolated standalone test installation, set:

```dotenv
OIDC_ENABLED="false"
APP_ALLOWED_HOSTS="signature-test.example.org"
LOCAL_ADMIN_EMAIL="admin@example.org"
LOCAL_ADMIN_PASSWORD_HASH='scrypt$...'
```

Generate the password hash interactively from the repository root:

```bash
python scripts/hash_password.py
```

 characters; do not put the plaintext password in the environment file. Use a password of at least 12 characters and protect `.env` with mode `0600`.

When OIDC is disabled, OpenSigWeave does not require or initialize OIDC discovery, client credentials, redirect URI, or Authentik API access. The `/auth` callback route is not registered. The `/login` page provides the local administrator login, and the local account receives the `signature-admins` and `signature-users` roles. Local profile attributes (name, title, phone, groups and claims) are empty; signature selection and administration can still be tested, but rules depending on external identity attributes cannot be meaningfully evaluated.

The local account is intended for standalone administration and isolated testing. Keep the portal behind HTTPS and restrict access to trusted networks. Do not expose it directly to the internet. Switching `OIDC_ENABLED` changes the authentication mode on restart; it does not migrate or link user identities between local and OIDC accounts.

### 2.4 Configure the OIDC provider in Authentik

Create an OAuth2/OpenID Provider and an application in Authentik.

Configure the provider for the Authorization Code flow and register this exact redirect URI:

```text
https://signature.example.org/auth
```

Replace the hostname with the real portal hostname. The URI must match `OIDC_REDIRECT_URI` exactly, including the `/auth` path and scheme.

The application expects the OIDC UserInfo response to contain:

- `sub`: stable user subject.
- `email`: the user's email address.
- `email_verified`: the JSON boolean `true`.
- `groups`: a JSON array of group names. Authentik's default `profile` scope mapping includes group membership.
- `attributes`: an object containing the profile fields `phone` and `title`.

**Important for Authentik 2025.10 and newer:** the default `email` scope mapping returns `email_verified: false` unless you explicitly provide a verified value. OpenSigWeave rejects login unless the claim is the JSON boolean `true`. Do not simply hard-code it to true for unverified addresses.

Create a custom scope mapping:

1. In Authentik Admin, open **Customization → Property Mappings** and create a **Scope Mapping**.
2. Name it `OpenSigWeave claims` and set its scope name to `opensigweave`.
3. Use this expression:

   ```python
   return {
       "email_verified": request.user.attributes.get("email_verified", False),
       "attributes": {
           "phone": request.user.attributes.get("phone", ""),
           "title": request.user.attributes.get("title", ""),
       },
   }
   ```

4. Save the mapping and add it to the OAuth2/OpenID Provider's **Advanced protocol settings → Scopes** (scope mappings).
5. In each Authentik user record, set the custom `email_verified` attribute to the boolean `true` only after the address has actually been verified. Set `phone` and `title` as user attributes when needed.
6. Keep the default `profile` and `email` mappings assigned to the provider. Confirm that the UserInfo response contains a `groups` array and the expected custom claims.

The configured scopes in `.env.example` are `openid email profile opensigweave`. A scope name is not enough by itself: the mapping must be assigned to the provider and the client must request that scope.

Official Authentik references: [create an OAuth2/OIDC provider](https://docs.goauthentik.io/add-secure-apps/providers/oauth2/create-oauth2-provider/), [OAuth2 provider scopes and email verification](https://docs.goauthentik.io/add-secure-apps/providers/oauth2/), and [provider property mappings](https://docs.goauthentik.io/add-secure-apps/providers/property-mappings/).

Create these groups in Authentik:

- `signature-users`
- `signature-admins`

Add ordinary portal users to `signature-users`. Add administrators to `signature-admins`; administrators can also be members of `signature-users` if they need the normal user view.

Copy the provider's Client ID and Client Secret into `.env`:

- `OIDC_CLIENT_ID`
- `OIDC_CLIENT_SECRET`

Set the following values to match your Authentik deployment:

- `AUTHENTIK_URL`: base URL only, for example `https://auth.example.org`.
- `AUTHENTIK_SLUG`: the application slug used in the Authentik provider URL.
- `OIDC_DISCOVERY_URL`: normally `https://auth.example.org/application/o/opensigweave/.well-known/openid-configuration`.
- `OIDC_REDIRECT_URI`: the exact HTTPS callback URI registered above.

When OIDC is enabled, the application also uses Authentik's API to retrieve employee profile fields and group/attribute data during signature compilation. Create a dedicated Authentik service account and API token:

1. Open **Directory → Users → New User** and select **Service Account**.
2. Create a dedicated role under **Directory → Roles**.
3. Grant that role the global **Can view User** permission. The current attribute provider queries `/api/v3/core/users/` by email and needs read access to user records; it does not need user write permissions.
4. Assign the role to the service account.
5. Open **Directory → Tokens and App passwords → Create**, select the service account and choose **API Token** as the intent.
6. Copy the token when it is shown and set `AUTHENTIK_API_TOKEN` in `.env`.

Do not use a personal administrator token. Prefer an expiring token and document its renewal date. See the [Authentik service account documentation](https://docs.goauthentik.io/users-sources/user/account-types/service-accounts/) and [permission management](https://docs.goauthentik.io/users-sources/access-control/manage_permissions/).

The Authentik attribute lookup uses the exact email address and requires exactly one active matching account. If the API token or lookup is technically unavailable, the engine falls back to the last successfully cached identity attributes in PostgreSQL. A confirmed missing or inactive account clears the cached attributes. If no cached record exists yet, the engine proceeds with empty profile attributes; the web login may still work, but signatures relying on those attributes will not render as expected.

### 2.5 Review the environment

Always configure these common values:

```dotenv
POSTGRES_PASSWORD="..."
SECRET_KEY="..."
ENGINE_API_KEY="..."
```

Then configure only the selected authentication mode:

- **OIDC enabled:** set `OIDC_ENABLED="true"` and configure `OIDC_DISCOVERY_URL`, `OIDC_REDIRECT_URI`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `AUTHENTIK_URL`, `AUTHENTIK_SLUG` and `AUTHENTIK_API_TOKEN`.
- **Local-only:** set `OIDC_ENABLED="false"`, `APP_ALLOWED_HOSTS` to the exact portal hostname, and configure `LOCAL_ADMIN_EMAIL` plus the generated `LOCAL_ADMIN_PASSWORD_HASH`. OIDC and Authentik values may be empty or omitted.

When OIDC is enabled, the hostname in `OIDC_REDIRECT_URI` is canonical. In local-only mode, `APP_ALLOWED_HOSTS` must contain at least one explicit hostname. Never use wildcards, schemes, ports or paths in this setting.

### 2.6 Validate and start Docker Compose

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

The reverse proxy must preserve the canonical Host header. The application validates it against the hostname in `OIDC_REDIRECT_URI` and any explicit `APP_ALLOWED_HOSTS` entries.

After HTTPS is active, open:

```text
https://signature.example.org/
```

You should be redirected to Authentik. Sign in with a user in one of the configured groups.

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

Compose creates two named volumes:

- `opensigweave-postgres-data`: PostgreSQL database.
- `opensigweave-signatures`: versioned HTML signature files.

The database stores metadata and hashes for the signature files. Back up both volumes as one consistent backup set. Restoring only one of them can leave database references without their corresponding files, or files without matching metadata.

Do not delete either volume when upgrading or recreating containers.

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

Use the exact hostname from `OIDC_REDIRECT_URI` in the browser. If a deliberate alias is required, add only that hostname to `APP_ALLOWED_HOSTS` and restart the application.

### OIDC callback fails

Check:

- `OIDC_DISCOVERY_URL` is reachable from the container and uses HTTPS.
- The Client ID and Client Secret match the Authentik provider.
- The redirect URI registered in Authentik exactly matches `OIDC_REDIRECT_URI`.
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
