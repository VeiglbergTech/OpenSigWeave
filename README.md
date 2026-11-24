<p align="center">
  <img src="branding/logo.png" alt="OpenSigWeave" width="520">
</p>

# OpenSigWeave

OpenSigWeave is an independent, self-hosted service for centrally managing email signatures. It provides a FastAPI administration portal, PostgreSQL-backed signature storage, rules, reusable signature groups, weighted rotation, scheduling, version history, OIDC/LDAP integration and mail-server adapters.

> **Release status:** The first planned release is **`2026.11-alpha`** (not yet published). This is **early access, not v1.0 Stable**. The SOGo/Mailcow, Authentik, CID and selected fail-closed paths have been tested in an isolated lab. LDAP, Outlook/Thunderbird and independent missing-guard protection remain unverified. See [release process](docs/RELEASE_PROCESS.md), [alpha notes](docs/RELEASE_NOTES_v2026.11.md) and the [technical acceptance matrix](docs/MAIL_USAGE_ACCEPTANCE_MATRIX.md).

## Documentation

**Planned documentation website:** [docs.veiglbergtech.com](https://docs.veiglbergtech.com) (**not yet published**; do not use the domain as a deployment prerequisite).

The new Astro/Starlight documentation is being developed in the separate [OpenSigWeave-Docs repository](https://github.com/VeiglbergTech/OpenSigWeave-Docs/tree/dev) with [Getting Started](https://github.com/VeiglbergTech/OpenSigWeave-Docs/tree/dev/src/content/docs/getting-started), installation, administration, operations, API, adapters and release information.

Until that documentation is approved and publicly published, the original documentation remains available in this repository:

- [API reference](docs/API.md), [adapter architecture](docs/ADAPTERS.md) and [application architecture](docs/ARCHITECTURE.md)
- [Mailcow/Rspamd staging integration](docs/MAILCOW_RSPAMD_INTEGRATION.md) and [mail-client compatibility](docs/CLIENT_COMPATIBILITY.md)
- [Release process and GHCR](docs/RELEASE_PROCESS.md), [upgrade and recovery](docs/UPGRADE.md), [backup/restore](docs/BACKUP_RESTORE_DESIGN.md), [administrator backup UI](docs/BACKUP_UI.md) and [prepared candidate release notes](docs/RELEASE_NOTES_v2026.11.md)

This transition intentionally leaves existing source documents in place until cross-repository migration, link checks and release promotion are complete. Internal audit reports are not automatically part of the public website.

## Features

- Domain-aware HTML signatures with immutable versions and restore-as-new-version.
- CKEditor visual editing and raw HTML mode with persistent signature image assets.
- Signature groups, weighted no-repeat selection, matching rules, date/time schedules and user overrides.
- Local emergency administrator; managed Authentik/generic OIDC and LDAP/Active Directory identity providers.
- Custom application branding with optional logo and favicon.
- Authenticated REST API for administrative automation and a separate adapter-neutral rendering engine API.
- Encrypted full backup/restore and selected nonsecret ZIP import/export.
- Rspamd integration with automated MIME regression tests. Postfix Milter and Stalwart MTA-hook adapters are **experimental**.

Mailcow/Rspamd still requires live staging acceptance before production mail-flow use. This project does not replace a mail server and does not provide sender authorization.

## Installation and channel-bound updates (published release)

The installer interactively requests only the initial administrator password (and offers hostname/email defaults), checks Docker/Compose, creates a protected `.env`, generates independent secrets including `BACKUP_LOCATIONS_KEY`, and preserves existing installations. For a headless first install, use `./install.sh --generate-admin-password` to store a one-time password in a protected local file that should immediately be moved offline. No backup, manually generated keys, OIDC or LDAP setup is required.

```bash
git clone --branch main https://github.com/VeiglbergTech/OpenSigWeave.git
cd OpenSigWeave
./install.sh
./generate_config.sh --check
./update.sh --check
./update.sh --prefetch
./update.sh --dry-run
./update.sh
```

Published `main` releases pull `ghcr.io/veiglbergtech/opensigweave:alpha` (or the optional `OSW_IMAGE_TAG` pin) and verify the image's source SHA against the checked-out Git commit. No application image is built locally during `main` installation or update. The initial image will exist only after an explicitly authorized main release tag is published. Unreleased `dev`/`beta` are still source-staging channels that use `compose.build.yaml` to build locally. **Backups remain optional**; with enabled backup rules, failed backups require explicit acceptance before continuing.

For a remote reverse proxy, bind `APP_BIND_ADDRESS` to the application host's **private-interface IP** and allow the application TCP port **only from the reverse-proxy private IP** at the host firewall. `TRUSTED_PROXY_CIDRS` controls trusted forwarded client-IP attribution but **does not make the proxy network boundary for you**. New database migrations are serialized using a one-off container; no volume removal, automatic rollback, or mail server reconfiguration occurs.

For the security constraints, backup prerequisites and recovery limits, see [installation and update reference](docs/INSTALL_UPDATE.md) and the [documentation repository](https://github.com/VeiglbergTech/OpenSigWeave-Docs/tree/dev/src/content/docs/installation).

## Branch and release policy

| Branch | Policy |
| --- | --- |
| `dev` | All development and documentation integration changes |
| `beta` | Only the tested `dev` state after regular and security CI both succeed |
| `main` | Only after real beta acceptance and **explicit** manual maintainer approval |

No automated promotion into `main`. Approved version tags are `2026.11-alpha`, `2026.11-alpha1`, `2026.11-alpha2`, then optional beta and stable tags. The self-hosted GHCR workflow publishes an immutable version tag plus exactly one matching rolling tag: `alpha`, `beta`, or `latest` (stable only). These are image tags, **not Git branches**. See [release process](docs/RELEASE_PROCESS.md). The separate documentation repository follows the same branch policy. CI: [application actions](https://github.com/VeiglbergTech/OpenSigWeave/actions) · [documentation actions](https://github.com/VeiglbergTech/OpenSigWeave-Docs/actions).

## Security and contributions

Use HTTPS and certificate validation for all identity providers and adapters. Do not put passwords, keys, internal network addresses or production identity data in issues, pull requests or public documentation. Keep GitHub Actions checks green and propose changes in `dev`.

License: [GPL-3.0](LICENSE).
