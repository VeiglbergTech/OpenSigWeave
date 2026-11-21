# Implementation status

**Project:** OpenSigWeave  
**Status:** Beta/release-candidate application. Core features are implemented and covered by automated validation; external production acceptance remains.  
**Branch policy:** `dev` is active development. `beta` is the deployment branch and is updated only from a fully green `dev` commit. `main` remains the release branch and is not automatically updated.

## Implemented

- **Application:** FastAPI portal/management API, PostgreSQL/Alembic persistence, SQLite migration compatibility and server-rendered administration UI.
- **Identity:** independent local emergency administrator; managed Authentik/generic OIDC and LDAP/Active Directory over LDAPS; separate per-domain login and profile providers; roles `signature-users` and `signature-admins`.
- **Signatures:** catalog with stable IDs, immutable source versions, domain scopes, weighted variants, personal/domain overrides and explicit message selectors.
- **Groups and rules:** reusable signature groups, weighted no-repeat selection, GROUP rules and `{{ group:slug }}` template placeholders.
- **Scheduling:** concrete UTC-backed time ranges targeting one signature or group, optional domain scope, enabled state and priority.
- **Branding:** persistent application name, accent color, PNG logo and PNG/ICO favicon with the bundled OpenSigWeave logo as fallback.
- **Editor:** self-hosted CKEditor 5 49.1.0 plus advanced raw-HTML editing.
- **HTML security:** exact editable source preservation, allowlist sanitization before preview/delivery, autoescaped profile values and sandboxed previews. Outlook conditional comments/VML remain source-only and are removed from delivered output.
- **Administration:** identity-provider, domain, signature, group, rule, scheduling and branding management; bounded selective ZIP export and validated additive import.
- **Security controls:** strong secret validation, CSRF, Secure/HttpOnly/SameSite session cookies, explicit trusted hosts, request limits, database-backed rate limiting, proxy-CIDR validation, server-side HTML sanitization and symlink-safe atomic signature storage.
- **Deployment:** non-root Python 3.12 application container, read-only root filesystem, bounded noexec/nosuid tmpfs, dropped Linux capabilities, `no-new-privileges`, PostgreSQL 17 and loopback-only default application binding.
- **Backups:** encrypted Docker-aware full backup, verification, fresh-target restore, retention and guarded systemd scheduling to mounted remote storage.
- **Rspamd:** external Lua adapter using the versioned engine API, now with parser-backed nested multipart plain/HTML text rewriting and attachment preservation. Signed/encrypted/embedded structures fail safely. Lua fixture + Python MIME parsing in CI; live Mailcow DKIM/SMTP acceptance still open.
- **Postfix:** experimental PyMilter sidecar with dedicated disposable-Postfix compatibility CI.
- **Stalwart:** experimental DATA-stage hook using the shared engine service and parser-backed MIME mutation.

## Automated verification

Application CI history:
- [dev](https://github.com/VeiglbergTech/OpenSigWeave/actions?query=branch%3Adev)
- [beta](https://github.com/VeiglbergTech/OpenSigWeave/actions?query=branch%3Abeta)

The application workflow covers:
- complete-Git-history secret scanning;
- Python dependency audit;
- Bandit static analysis;
- Python and browser-JavaScript syntax checks;
- Alpine CSP-expression checks and Chromium policy-violation observation;
- PostgreSQL and SQLite migrations;
- Docker build/startup and bundled-branding checks;
- Chromium administration workflows, including CKEditor, Scheduling and Branding;
- encrypted full backup, verification and fresh restore;
- full regression tests.

Dedicated Postfix compatibility CI verifies PyMilter/libmilter behavior, real SMTP/Milter protocol exchange, SMTP AUTH identity propagation, Postfix sender-login enforcement and final local delivery through the actual adapter with a mock engine.

A successful workflow is evidence for the tested commit. It is not a penetration test, live production-mail-flow certification or a substitute for the acceptance work below.

## Release acceptance still open

| Area | Remaining acceptance |
|---|---|
| Identity | Live Authentik/generic OIDC and LDAP/Active Directory sign-in/logout and group mapping in the intended environment |
| Browser/CSP | CSP remains Report-Only pending broader supported-browser/deployment acceptance |
| Email clients | Real SOGo, Outlook and Thunderbird reply/forward rendering with delivered MIME |
| Outlook HTML | Security-reviewed conditional-comment/VML output policy if Outlook-only markup is ever to be delivered |
| Rspamd | Disposable Mailcow sender authorization, processing order, idempotency and final DKIM |
| Postfix | Live HTTPS engine/TLS trust, production filter order, failure policy, load and final DKIM |
| Stalwart | Live SMTP/DKIM end-to-end acceptance and filter-chain composition |
| Backup/DR | Two-host restore drill and real NFS/SMB disconnect/remount/recovery |
| Operations | Reverse-proxy topology validation, realistic throughput/load and dynamic security testing |

## Scope boundaries

The beta application does not claim:
- production certification for any mail-server adapter;
- exactly-once signature injection across independent message re-submissions;
- Outlook VML/conditional-comment delivery support;
- recurring annual/weekly scheduling;
- SCIM provisioning or nested LDAP group expansion;
- complete CLI parity with the web administration UI.

See [README](../README.md), [architecture](ARCHITECTURE.md), [API](API.md), [adapters](ADAPTERS.md), [backup/restore](BACKUP_RESTORE_DESIGN.md), [security review](WEB_AND_RSPAMD_REVIEW.md) and [repository audit](REPOSITORY_AUDIT.md).
