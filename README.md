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
- [Upgrade and recovery](docs/UPGRADE.md), [backup/restore](docs/BACKUP_RESTORE_DESIGN.md) and [prepared candidate release notes](docs/RELEASE_NOTES_v2026.11.md)

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

## Quick start (beta / release candidate)

Requirements: Linux, Docker Engine, Docker Compose v2, Git and an HTTPS reverse proxy.

```bash
git clone --branch beta --single-branch https://github.com/VeiglbergTech/OpenSigWeave.git
cd OpenSigWeave
cp .env.example .env
chmod 600 .env
python scripts/hash_password.py
```

Generate **independent** random values for the PostgreSQL password, session signing key and engine API key. Configure at minimum `POSTGRES_PASSWORD`, `SECRET_KEY`, `ENGINE_API_KEY`, `APP_ALLOWED_HOSTS`, `LOCAL_ADMIN_EMAIL` and `LOCAL_ADMIN_PASSWORD_HASH` in `.env`. Set an optional distinct `MANAGEMENT_API_KEY` only when trusted unattended administration is required.

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 opensigweave
```

The app listens on container port 8085, bound to the host loopback by default; PostgreSQL is not exposed to the host. For a remote reverse proxy, set `APP_BIND_ADDRESS` to the application host's **private-interface IP** and restrict TCP/8085 **only from the reverse-proxy private IP** with the host firewall. `TRUSTED_PROXY_CIDRS` controls forwarded client-IP attribution but **does not make the proxy network boundary for you**. Consult the [upgrade instructions](docs/UPGRADE.md), [Mailcow staging integration guide](docs/MAILCOW_RSPAMD_INTEGRATION.md) and [full backup notes](docs/BACKUP_RESTORE_DESIGN.md) before a deployment or upgrade.

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
