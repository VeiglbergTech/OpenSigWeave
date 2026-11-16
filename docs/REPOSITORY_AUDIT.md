# Repository and security audit

**Repository:** `VeiglbergTech/OpenSigWeave`  
**Review date:** 2026-11-16  
**Scope:** Current standalone preview. This is an engineering review, not a penetration test or security certification.

## Current implementation

- FastAPI portal, management API, PostgreSQL/Alembic and SQLite compatibility.
- Per-domain Authentik/generic OIDC or LDAPS login and independent profile providers; local emergency administrator.
- Versioned signature HTML storage, sanitization, catalog, groups, rules and weighted selection.
- CSRF/session/host controls, request limits, shared database rate limiting and proxy-CIDR-aware client identity.
- Encrypted full backups, fresh-target restore, verified retention and guarded remote-mount scheduling.
- Optional Rspamd adapter, isolated from the standalone application and Mailcow configuration.

## Security and operational findings

| Area | Verified implementation | Remaining work |
|---|---|---|
| Authentication/authorization | Managed OIDC, LDAPS, local emergency login and server-side role checks | Live provider sign-in/logout and group mapping |
| Browser security | CSRF, secure sessions, trusted hosts, response headers and server-side HTML sanitization | CSP remains report-only; two Quill 1.3.7 style-attribute reports remain |
| Request protection | Body limits, shared database rate limits and trusted-proxy CIDR checks | Validate thresholds and proxy behavior on the target deployment |
| Signature storage | Path validation, symlink rejection, atomic writes and SHA-256 integrity checks | Real cross-host restore and remote-filesystem failure tests |
| Backups | Encrypted archive, fresh-target restore, checksum verification and fail-closed retention | Two-host drill and real NFS/SMB acceptance |
| Mail flow | HTTPS engine API; multipart messages fail closed and remain unchanged | SMTP sender authorization, Rspamd ordering, idempotency and DKIM in disposable Mailcow |
| Container | Non-root user, read-only root filesystem, dropped capabilities, no-new-privileges and loopback binding | Deployment-specific runtime review |

See [web and Rspamd security review](WEB_AND_RSPAMD_REVIEW.md) for detailed trust boundaries and test cases, and [backup/restore design](BACKUP_RESTORE_DESIGN.md) for recovery guarantees.

## Historical credential

A Rspamd engine credential was present in historical Git content. It is not reproduced here. Treat it as compromised and rotate it anywhere it may have been deployed, copied or stored. Git history was not rewritten; the known historical finding is allowlisted for scanning, while new findings remain subject to CI.

## CI evidence and limits

- Latest `dev` validation: push run [#1878](https://github.com/VeiglbergTech/OpenSigWeave/actions/runs/43814178993) passed on `562c25dfe132f47ae932136ec879088be48af0ed`.
- `beta` was fast-forwarded to that same commit; push run [#1879](https://github.com/VeiglbergTech/OpenSigWeave/actions/runs/43814609508) is pending and must pass before `beta` is called CI-validated at this baseline.
- The workflow covers secret/dependency scans, syntax, migrations, Docker health, browser workflows, backup/restore and regression tests.
- The latest `dev` commit changes documentation and CI configuration only; application behavior is unchanged.

Automated local CI is not live LDAP/OIDC validation, a cross-host restore drill, real NFS/SMB testing, SMTP/DKIM integration, a dynamic penetration test or production certification.

## Remaining actions

1. Rotate the historical Rspamd key wherever deployed.
2. Verify live LDAP/OIDC login, group mapping and logout.
3. Resolve Quill CSP reports without broad `unsafe-inline` exceptions.
4. Complete the two-host and real NFS/SMB backup acceptance.
5. Test Rspamd sender authorization, MIME behavior, idempotency and DKIM in disposable Mailcow.
6. Validate rate-limit thresholds and reverse-proxy Host/forwarded-header handling.
7. Perform focused dynamic security and realistic load testing.
