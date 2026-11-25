<p align="center">
  <img src="branding/logo.png" alt="OpenSigWeave – self-hosted email signature management for Mailcow and Rspamd" width="520">
</p>

# OpenSigWeave – Self-Hosted Email Signature Management

**OpenSigWeave** is a GPL-3.0-licensed, self-hosted **email signature management** application for organizations that want centrally managed HTML email signatures. It combines **Mailcow/Rspamd integration**, an administration web interface, **LDAP/Active Directory and OIDC/Authentik identity support**, domain-specific rules, rotating signatures and scheduled campaigns.

Manage email signatures in one place instead of configuring every user and mail client separately. OpenSigWeave uses a Python/FastAPI service, PostgreSQL, Docker Compose and a mail-server-independent signature engine with adapters.

> **Release status: `2026.11-alpha` is planned, not yet published.** OpenSigWeave is in **early access**, not a stable or production-certified release. Mailcow/SOGo, Authentik, rule selection, rotation, scheduling, CID images and selected failure/recovery cases have been tested in an isolated environment. LDAP, Outlook/Thunderbird and independent enforcement when both the Rspamd injector and its guard are missing still need testing. Read the [alpha release notes](docs/RELEASE_NOTES_v2026.11.md), [release acceptance checklist](docs/RELEASE_ACCEPTANCE_v2026.11.md) and [mail usage acceptance matrix](docs/MAIL_USAGE_ACCEPTANCE_MATRIX.md).

## Email signature management features

- **Centralized HTML signatures:** manage signatures for multiple email domains through an administration portal.
- **WYSIWYG email signature editor:** use CKEditor, raw HTML editing, uploaded assets and version history.
- **Dynamic user attributes:** use configured OIDC/Authentik and LDAP/Active Directory attributes, with per-domain fallback values when information is missing.
- **Signature rules and groups:** choose signatures by sender/domain, configured conditions, user overrides and weighted rotation without immediate repetition where applicable.
- **Scheduled email signatures:** apply time-based rules, for example a seasonal signature campaign.
- **Mail-server integration:** a Mailcow/Rspamd adapter for server-side signature injection, with an adapter-neutral rendering API. Postfix Milter and Stalwart MTA hook adapters are **experimental**.
- **Administration and recovery:** local administrator sign-in, optional identity provider integration, authenticated management API, custom branding, selective import/export and encrypted backup/restore.

OpenSigWeave **does not replace an SMTP/IMAP server**, authorize mail senders or guarantee signatures when mail-server filters are bypassed. Review the [Mailcow/Rspamd integration guide](docs/MAILCOW_RSPAMD_INTEGRATION.md) before changing live mail flow.

## How OpenSigWeave works

1. An administrator creates HTML signatures, groups, domain settings and matching rules in the web interface.
2. The mail-server adapter calls OpenSigWeave's signature engine for the relevant message and sender.
3. The engine selects the configured signature, resolves available identity attributes and domain fallbacks, and returns the rendered content.
4. The adapter incorporates the result into the supported email/MIME structure. The observed Mailcow/SOGo integration supports inline CID images and nested replies, but other clients and mail paths require their own testing.

**Typical use cases:** company-wide email signatures, multi-domain signature management, rotating promotional banners, seasonal campaigns and user-specific signatures based on directory data.

## Compatibility and project status

| Component | Status |
| --- | --- |
| Mailcow with Rspamd, SOGo | Tested in an isolated lab; no general production certification |
| Authentik / OIDC identity | Live functionality observed |
| LDAP / Active Directory | Integration implemented; live acceptance pending |
| Outlook and Thunderbird | Compatibility work ongoing; Outlook 2016 live test deferred |
| Postfix Milter and Stalwart MTA hook | Experimental adapters |
| Docker Compose and PostgreSQL | Used for deployment and CI integration tests |

Refer to [client compatibility](docs/CLIENT_COMPATIBILITY.md), [adapter architecture](docs/ADAPTERS.md) and the [acceptance matrix](docs/MAIL_USAGE_ACCEPTANCE_MATRIX.md) for limitations.

## Install OpenSigWeave with Docker Compose

**Requirements:** Linux (x86_64/aarch64), Docker Engine 24+, Docker Compose 2.20+, Git, Python 3, Docker daemon access, at least 2 GiB RAM and 6 GiB available disk space. Your mail server and TLS reverse proxy must be configured separately.

**After the first approved `2026.11-alpha` package is published**, the release installation is:

```bash
git clone --branch main https://github.com/VeiglbergTech/OpenSigWeave.git
cd OpenSigWeave
./install.sh --hostname signatures.example.com
./generate_config.sh --check
```

The installer creates a protected `.env`, generates the required application and database secrets, provisions PostgreSQL and starts the service. Published releases **pull the prebuilt application image from GitHub Container Registry**, rather than building it on the deployment host. The GHCR image is not available before an authorized main release is actually published. For development, `dev` and `beta` use a separate source-build Compose overlay.

### Configure the listening address and reverse proxy

**Default:** `APP_BIND_ADDRESS=0.0.0.0` listens on **all IPv4 network interfaces**. This does **not** enable HTTPS or configure your firewall. **Block public access to the application port** and allow only your intended reverse proxy. For a reverse proxy running on the same host, select `127.0.0.1`.

Both entrypoints ask for the listening address **during the initial interactive
configuration**, with `0.0.0.0` offered as the default:

```bash
./install.sh
# Alternatively, create the configuration first:
./generate_config.sh
```

At the `Application bind IP` question, press Enter to accept `0.0.0.0`,
or enter `127.0.0.1` for a reverse proxy on the same host. No
`--bind-address` option is required or supported.

The installation wizard also configures `APP_ALLOWED_HOSTS` and optional `TRUSTED_PROXY_CIDRS`. Trust only the IP address or narrow CIDR range of an actual reverse proxy; trusting a proxy does not replace firewall rules. The bind-address question is asked for **new** configurations only. To change an existing deployment, edit `APP_BIND_ADDRESS` in the protected `.env` and recreate the affected application container; upgrades preserve existing values.

For all options, unattended installation, security considerations and recovery steps, see [installation and update documentation](docs/INSTALL_UPDATE.md).

### Update a released installation

```bash
./update.sh --check
./update.sh --prefetch
./update.sh --dry-run
./update.sh
```

Update backups remain **optional**. If a configured backup fails, continuing requires an explicit decision. On `main`, the updater verifies that the pulled GHCR image matches the approved Git revision before applying changes. See [upgrade and recovery](docs/UPGRADE.md) and [GHCR release process](docs/RELEASE_PROCESS.md).

## Documentation

The in-repository documentation remains the current reference while the separate [OpenSigWeave documentation repository](https://github.com/VeiglbergTech/OpenSigWeave-Docs/tree/dev) is being prepared. The planned website, [docs.veiglbergtech.com](https://docs.veiglbergtech.com), is **not yet published**.

| Documentation | Description |
| --- | --- |
| [Installation and updates](docs/INSTALL_UPDATE.md) | Setup, configuration, backup policy and upgrades |
| [Mailcow and Rspamd integration](docs/MAILCOW_RSPAMD_INTEGRATION.md) | Mail-server configuration, SMTP behavior and staging tests |
| [API reference](docs/API.md) | Administrative and integration API |
| [Adapter architecture](docs/ADAPTERS.md) | Supported engine contract and adapter design |
| [Backup and restore](docs/BACKUP_RESTORE_DESIGN.md) | Backup scope and recovery limits |
| [Release and GHCR publishing](docs/RELEASE_PROCESS.md) | Alpha/beta/stable version tags, images and approval policy |

## Frequently asked questions

### Does OpenSigWeave work without LDAP?

Yes. The application provides a local administrator login, and identity providers are optional. Directory-based signatures can use configured providers or supported domain fallback attributes.

### Can OpenSigWeave add email signatures to Mailcow?

The Mailcow/Rspamd integration has passed selected live SOGo/Mailcow tests. It is still **early access**, and you should validate the full SMTP and DKIM flow before using it for critical or production email.

### Do I need to build the Docker image myself?

Not for an **approved published release**: the `main` installer pulls the corresponding image from GHCR and verifies its source commit. Development/staging checkouts can still build locally. The first release image is not published yet.

### Is OpenSigWeave a replacement for CodeTwo or a mail server?

It targets the central management and server-side insertion of email signatures, but it does not claim feature parity with commercial services or replace a mail server.

## Development, security and license

The project uses exactly three Git branches: **`dev`** for development, **`beta`** for CI-verified code and **`main`** for explicitly approved releases. Release versions follow `YYYY.MM-alpha`, `YYYY.MM-alpha1`, `YYYY.MM-beta` and `YYYY.MM`. Moving GHCR image aliases are `alpha`, `beta` and `latest` (stable only). The image publishing CI never automatically approves `main`.

Before exposing any management service, configure HTTPS, an appropriate firewall and trusted proxy IPs. Never commit passwords, `.env` files or private keys. Report security concerns privately rather than posting credentials or customer data in public issues.

**License:** [GNU General Public License v3.0](LICENSE). **Project:** [VeiglbergTech/OpenSigWeave](https://github.com/VeiglbergTech/OpenSigWeave).
