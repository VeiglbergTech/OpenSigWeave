<p align="center">
  <img src="branding/logo.png" alt="OpenSigWeave" width="520">
</p>

# OpenSigWeave

OpenSigWeave is an independent, self-hosted service for centrally managing email signatures. It provides a FastAPI administration portal, PostgreSQL-backed signature storage, rules, reusable signature groups, weighted rotation, scheduling, version history, OIDC/LDAP integration and mail-server adapters.

> **Release status:** v2026.11 / v1.0 is **not released** or production-certified. Automated tests cover substantial functionality, but real identity-provider, DKIM, SMTP delivery and mail-client acceptance remain outstanding. See [candidate release notes](docs/RELEASE_NOTES_v2026.11.md) and the [release acceptance checklist](docs/RELEASE_ACCEPTANCE_v2026.11.md).

## Documentation

**Planned documentation website:** [docs.veiglbergtech.com](https://docs.veiglbergtech.com) (**not yet published**; do not use the domain as a deployment prerequisite).

The new Astro/Starlight documentation is being developed in the separate [OpenSigWeave-Docs repository](https://github.com/VeiglbergTech/OpenSigWeave-Docs/tree/dev) with [Getting Started](https://github.com/VeiglbergTech/OpenSigWeave-Docs/tree/dev/src/content/docs/getting-started), installation, administration, operations, API, adapters and release information.

Until that documentation is approved and publicly published, the original documentation remains available in this repository:

- [API reference](docs/API.md), [adapter architecture](docs/ADAPTERS.md) and [application architecture](docs/ARCHITECTURE.md)
- [Mailcow/Rspamd staging integration](docs/MAILCOW_RSPAMD_INTEGRATION.md) and [mail-client compatibility](docs/CLIENT_COMPATIBILITY.md)
- [Upgrade and recovery](docs/UPGRADE.md), [backup/restore](docs/BACKUP_RESTORE_DESIGN.md), [administrator backup UI](docs/BACKUP_UI.md) and [prepared candidate release notes](docs/RELEASE_NOTES_v2026.11.md)

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

## Installation and channel-bound updates (release candidate)

The installer interactively requests only the initial administrator password (and offers hostname/email defaults), checks Docker/Compose, creates a protected `.env`, generates independent secrets including `BACKUP_LOCATIONS_KEY`, and preserves existing installations. For a headless first install, use `./install.sh --generate-admin-password` to store a one-time password in a protected local file that should immediately be moved offline. No backup, manually generated keys, OIDC or LDAP setup is required.

```bash
git clone --branch beta https://github.com/VeiglbergTech/OpenSigWeave.git
cd OpenSigWeave
./install.sh
./generate_config.sh --check
./update.sh --check
./update.sh --prefetch
./update.sh --dry-run
./update.sh
```

`dev` and `beta` build Docker images locally; the updater also builds checked-out `main` locally until an independently verified GHCR release mechanism is introduced. The installer never switches channels automatically. **Backups are optional.** Updates without any active Backup Rule proceed normally and without backup warnings. If enabled Backup Rules exist, the updater uses the selected rule and its Local/SMB destination, then verifies the retained encrypted archive and PostgreSQL dump. If it fails, the update stops and requires an explicit administrator confirmation; `--yes` alone never bypasses the error. For unattended overrides only, `--yes --continue-without-backup` is supported. Do not confuse an update without backup with a recoverable restore point.

For a remote reverse proxy, bind `APP_BIND_ADDRESS` to the application host's **private-interface IP** and allow the application TCP port **only from the reverse-proxy private IP** at the host firewall. `TRUSTED_PROXY_CIDRS` controls trusted forwarded client-IP attribution but **does not make the proxy network boundary for you**. New database migrations are serialized using a one-off container; no volume removal, automatic rollback, or mail server reconfiguration occurs.

For the security constraints, backup prerequisites and recovery limits, see [installation and update reference](docs/INSTALL_UPDATE.md) and the [documentation repository](https://github.com/VeiglbergTech/OpenSigWeave-Docs/tree/dev/src/content/docs/installation).

## Branch and release policy

| Branch | Policy |
| --- | --- |
| `dev` | All development and documentation integration changes |
| `beta` | Only the tested `dev` state after regular and security CI both succeed |
| `main` | Only after real beta acceptance and **explicit** manual maintainer approval |

No automated promotion into `main`. No unapproved releases or tags. The separate documentation repository follows the same policy. CI: [application actions](https://github.com/VeiglbergTech/OpenSigWeave/actions) · [documentation actions](https://github.com/VeiglbergTech/OpenSigWeave-Docs/actions).

## Security and contributions

Use HTTPS and certificate validation for all identity providers and adapters. Do not put passwords, keys, internal network addresses or production identity data in issues, pull requests or public documentation. Keep GitHub Actions checks green and propose changes in `dev`.

License: [GPL-3.0](LICENSE).
