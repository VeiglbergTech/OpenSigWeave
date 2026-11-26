<p align="center">
  <img src="branding/logo.png" alt="OpenSigWeave – self-hosted email signature management for Mailcow and Rspamd" width="520">
</p>

# OpenSigWeave – Open-Source Email Signature Manager for Mailcow & Rspamd

**OpenSigWeave** is a free, open-source, self-hosted **email signature manager** for organizations that need to create, manage and automatically insert professional **HTML email signatures**. Its first integration targets **Mailcow** and **Rspamd**, enabling centrally controlled, server-side email signatures instead of maintaining a different signature on every device.

Manage company-wide email signatures across multiple domains with a web-based signature editor, sender and domain rules, signature groups, rotation and scheduled campaigns. OpenSigWeave can resolve user information from **LDAP / Active Directory** and **Authentik / OIDC**, with configurable per-domain fallback attributes.

The application runs with **Docker Compose**, **PostgreSQL** and **Python / FastAPI**. Its signature rendering engine is separated from mail-server adapters so support can be developed beyond Mailcow/Rspamd. OpenSigWeave is licensed under the [GNU GPL v3.0](LICENSE) and builds on the [original project by TylerHats](https://github.com/TylerHats/OpenSigWeave).

> **Latest published release: [OpenSigWeave 2026.11-alpha](https://github.com/VeiglbergTech/OpenSigWeave/releases/tag/2026.11-alpha) (25 November 2026).** GitHub Actions successfully published the Docker image to GitHub Container Registry (GHCR) as `ghcr.io/veiglbergtech/opensigweave:2026.11-alpha` and the moving `:alpha` alias. This is an **early-access pre-release**, not a stable or production-certified version. Mailcow/SOGo, Authentik, rule selection, rotation, scheduling, CID images and selected failure/recovery cases have been tested in an isolated environment. LDAP, Outlook/Thunderbird and independent enforcement when both the Rspamd injector and its guard are missing still need testing. Read the [alpha release notes](docs/RELEASE_NOTES_v2026.11.md), [release acceptance checklist](docs/RELEASE_ACCEPTANCE_v2026.11.md) and [mail usage acceptance matrix](docs/MAIL_USAGE_ACCEPTANCE_MATRIX.md).

## Features: centralized HTML email signature management

- **Centralized business email signatures:** create and manage reusable HTML signatures for multiple domains and senders in one administration portal.
- **WYSIWYG email signature editor:** use CKEditor, raw HTML editing, uploaded assets and version history.
- **Dynamic user attributes:** use configured OIDC/Authentik and LDAP/Active Directory attributes, with per-domain fallback values when information is missing.
- **Signature rules and groups:** choose signatures by sender/domain, configured conditions, user overrides and weighted rotation without immediate repetition where applicable.
- **Scheduled email signatures:** apply time-based rules, for example a seasonal signature campaign.
- **Mailcow/Rspamd signature injection:** insert signatures during mail processing using the Rspamd adapter and an adapter-neutral rendering API. Postfix Milter and Stalwart MTA hook adapters are **experimental**.
- **Administration and recovery:** local administrator sign-in, optional identity provider integration, authenticated management API, custom branding, selective import/export and encrypted backup/restore.

On Mailcow/Rspamd, `failure_policy = "enabled"` temporarily rejects
technical signature-processing failures, while `disabled` does not. The
former `standard` and `tempfail` options remain supported as aliases.
A [host-side module health check](docs/MAILCOW_FAILURE_POLICIES.md) verifies
module files and configuration after restarts/updates without granting
the web application Docker daemon access. A successful health check does
not replace an SMTP signing test.

OpenSigWeave **does not replace an SMTP/IMAP server**, authorize mail senders or guarantee signatures when mail-server filters are bypassed. Review the [Mailcow/Rspamd integration guide](docs/MAILCOW_RSPAMD_INTEGRATION.md) before changing live mail flow.

## How OpenSigWeave works

1. An administrator creates HTML signatures, groups, domain settings and matching rules in the web interface.
2. The mail-server adapter calls OpenSigWeave's signature engine for the relevant message and sender.
3. The engine selects the configured signature, resolves available identity attributes and domain fallbacks, and returns the rendered content.
4. The adapter incorporates the result into the supported email/MIME structure. The observed Mailcow/SOGo integration supports inline CID images and nested replies, but other clients and mail paths require their own testing.

### What can you use OpenSigWeave for?

OpenSigWeave is designed for IT administrators and organizations that want consistent email branding without maintaining signatures on each employee's devices. Typical uses include company-wide email signatures, centralized multi-domain signature management, directory-based contact details, rotating marketing banners and scheduled seasonal campaigns.

The first integration is Mailcow with Rspamd. Other mail-server adapters are not considered production-ready merely because the core engine is adapter-neutral.

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

## Install OpenSigWeave on Linux with Docker Compose

**Requirements:** Linux (x86_64/aarch64), Docker Engine 24+, Docker Compose 2.20+, Git, Python 3, Docker daemon access, at least 2 GiB RAM and 6 GiB available disk space. Your mail server and TLS reverse proxy must be configured separately.

**The approved `2026.11-alpha` package is published.** For the verified release source on `main`, install with:

```bash
git clone --branch main https://github.com/VeiglbergTech/OpenSigWeave.git
cd OpenSigWeave
./install.sh
./generate_config.sh --check
```

The interactive installer asks for the hostname, administrator account, bind address, allowed hosts and trusted proxies. It creates a protected `.env`, generates the required application and database secrets, provisions PostgreSQL and starts the service. The published `main` release **pulls the prebuilt application image from GitHub Container Registry** rather than building it on the deployment host. Development branches (`dev` and `beta`) use a separate source-build Compose overlay.

**Published Docker image:** `ghcr.io/veiglbergtech/opensigweave:2026.11-alpha` (immutable release version) or `ghcr.io/veiglbergtech/opensigweave:alpha` (moving alpha alias). To verify that you can access the package, run:

```bash
docker pull ghcr.io/veiglbergtech/opensigweave:2026.11-alpha
```

The default Docker Compose tag is `alpha`. After generating the protected `.env`, you can pin the exact version with `OSW_IMAGE_TAG=2026.11-alpha`. The installer verifies that the image's embedded source revision matches the **exact Git commit checked out on `main`**. The first alpha image was built from commit `29f43e9c5df70fa09576422b86f55c2e4d5c9e3c`; newer `main` commits require their own approved, source-matched published image. If GHCR returns an authentication error, check the package visibility or authenticate with an authorized read-only GitHub token.

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

## Frequently asked questions about OpenSigWeave

### What is a server-side email signature manager?

A server-side email signature manager centrally chooses and inserts signatures when an email passes through a supported mail-processing integration. This can reduce the need to configure a signature separately in each desktop, mobile or webmail client. Actual placement and formatting depend on the MIME structure, sending client and adapter; see the [compatibility notes](docs/CLIENT_COMPATIBILITY.md).

### Does OpenSigWeave work without LDAP?

Yes. The application provides a local administrator login, and identity providers are optional. Directory-based signatures can use configured providers or supported domain fallback attributes.

### Can I centrally manage Mailcow email signatures with OpenSigWeave?

Yes, the Mailcow/Rspamd integration can insert centrally managed signatures on supported outbound mail paths and has passed selected live SOGo/Mailcow tests. It is still **early access**, and you should validate the full SMTP and DKIM flow before using it for critical or production email.

### Do I need to build the Docker image myself?

No. The first prebuilt alpha image has been published as `ghcr.io/veiglbergtech/opensigweave:2026.11-alpha` with the `alpha` alias. The `main` installer pulls the image from GHCR and verifies its exact source commit; development/staging checkouts can still build locally. See the [published alpha release](https://github.com/VeiglbergTech/OpenSigWeave/releases/tag/2026.11-alpha).

### Is OpenSigWeave a replacement for CodeTwo or a mail server?

It targets the central management and server-side insertion of email signatures, but it does not claim feature parity with commercial services or replace a mail server.

## Credits and acknowledgments

OpenSigWeave builds on the **[original OpenSigWeave repository by TylerHats](https://github.com/TylerHats/OpenSigWeave)**. Thank you to **[TylerHats](https://github.com/TylerHats)** for creating the project and its original approach to centrally managed, server-inserted email signatures. This repository is a separately maintained continuation with significant changes to architecture, editor, identity integrations, deployment and administration features.

The original project's **Rspamd signature injection engine** drew heavily on the MIME-rebuild array logic developed by the **[mailcow team](https://github.com/mailcow/mailcow-dockerized)** for the **`MOO_FOOTER`** module. We retain that original acknowledgment and thank the mailcow / Servercow contributors for their work on the open-source email ecosystem. See the [mailcow project](https://mailcow.email/) and [TylerHats' original README](https://github.com/TylerHats/OpenSigWeave#-acknowledgments--licensing).

**Project independence:** OpenSigWeave is not an official mailcow product; credits do not imply an endorsement or formal affiliation with mailcow, TylerHats or other named projects.

## Development, security and license

The project uses exactly three Git branches: **`dev`** for development, **`beta`** for CI-verified code and **`main`** for explicitly approved releases. Release versions follow `YYYY.MM-alpha`, `YYYY.MM-alpha1`, `YYYY.MM-beta` and `YYYY.MM`. Moving GHCR image aliases are `alpha`, `beta` and `latest` (stable only). The image publishing CI never automatically approves `main`.

Before exposing any management service, configure HTTPS, an appropriate firewall and trusted proxy IPs. Never commit passwords, `.env` files or private keys. Report security concerns privately rather than posting credentials or customer data in public issues.

**License:** [GNU General Public License v3.0](LICENSE). **Source code:** [VeiglbergTech/OpenSigWeave](https://github.com/VeiglbergTech/OpenSigWeave). **Bugs and feature requests:** [GitHub Issues](https://github.com/VeiglbergTech/OpenSigWeave/issues).
