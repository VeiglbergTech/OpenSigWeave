# CLI / REST / Web feature decisions for v1.0

This assessment reflects the implementation on `dev`. The objective is *automation coverage*, not forced CLI/Web parity. The existing FastAPI routes are the authoritative business implementation: adding CLI copies of the same CRUD logic would create security and maintenance drift.

## Security boundary

- Portal actions: signed administrator session and CSRF header for state-changing requests.
- Headless administrator actions: optional `MANAGEMENT_API_KEY` in the `X-Management-Key` header, only for `/api/admin/*` and `/domains/*`. Full administrator rights; disabled by default and deliberately separate from the mail transport `ENGINE_API_KEY`.
- Mail-adapter signature resolution: `X-Engine-Key` with `ENGINE_API_KEY`; does **not** grant administrator privileges.
- Personal signature operations: signed user browser session with appropriate group. A management key is not a substitute for the user's identity.
- Public asset access: intentionally unauthenticated for mail-client retrieval. No write access.
- Backups: privileged, local Docker/age/filesystem operator CLI; not a remote HTTP operation.

## Feature matrix

| Feature | Web UI | API | CLI | v1.0 rationale |
|---|---|---|---|---|
| Domains and domain settings | Yes | Yes | No | Existing REST CRUD can be automated; no separate CLI logic |
| Signature creation, metadata | Yes | Yes | No | HTTP catalog reuses the same validations and persistence |
| Signature versions, preview, restore | Yes | Yes | No | REST supports safe version history; no local CLI needed |
| Signature assets (upload/public URLs) | Yes | Yes | No | Editor handles uploads; API supports integrations; public GET for mail clients |
| Signature groups and rotation weights | Yes | Yes | No | REST mutations are scriptable, engine owns rotation |
| Rules and priority | Yes | Yes | No | Rules are data/configuration, already available through REST |
| Scheduling | Yes | Yes | No | API automates time windows; engine evaluates them without a cron job |
| Branding | Yes | Yes | No | Primarily interactive; REST exists for configuration management |
| Identity providers and assignments | Yes | Yes | No | Sensitive admin operation; full-admin REST available, never via engine key |
| Personal signature overrides | Yes | Yes (session-only) | No | Changes must remain tied to a real user's authenticated identity |
| Administrative overrides | Yes | Yes | No | Existing admin REST endpoints support automation |
| Selective component export/import | Yes | Yes | No | REST handles component migration; not full disaster recovery |
| Full encrypted backup | No | No | `backup_cli.py create` | Requires Docker, age and local access: CLI is appropriate |
| Full restore to fresh deployment | No | No | `backup_cli.py restore` | Deliberately local/destructive, not safe as a web mutation |
| Archive packaging | No | No | `backup_cli.py package` | Low-level operator operation for prepared staging directories |
| Archive integrity verification | No | No | `backup_cli.py verify` | Offline verification belongs with backup tooling |
| Archive decryption | No | No | `backup_cli.py decrypt` | Sensitive filesystem task should not be remotely exposed |
| Backup retention | No | No | `backup_cli.py prune` | Local retention with dry-run by default |
| Local admin password hash generation | No | No | `scripts/hash_password.py` | One-time bootstrap utility, not app CRUD |
| Rendered signature lookup | No | Yes (engine API) | No | Internal adapter contract; not administrator CRUD |
| SMTP/MIME injection | No | Yes (adapter hook where supported) | No | Adapter-specific processing, no UI/CLI abstraction needed |

`Yes` means the corresponding facility exists; individual controls and endpoints can have different validation and permissions. A feature's presence in the API does not imply that the web UI exposes every field.

## Existing CLI commands and v1.0 decisions

`backup_cli.py create`, `restore`, `package`, `verify`, `decrypt`, and `prune` remain supported. `scripts/hash_password.py` remains a bootstrap helper, not a second application management CLI. Scheduled backup automation is implemented by `scripts/backup_to_mounted_storage.sh` and systemd service/timer, which invoke existing backup operations. No obsolete user-facing CLI commands were found that justify removal.

Missing dedicated CLI commands for domains, signatures, groups, rules, schedules, branding and provider configuration are **intentional**. They already have API operations and adding CLI copies would not add meaningful capability. Use standard HTTP automation clients and service-specific secrets. Fine-grained management API scopes, token issuance/rotation UI, an OpenAPI public schema and any broad CLI wrapper are *out of scope for v1.0*; consider them only if concrete automation needs justify the additional maintenance and security surface.

See [API reference](API.md), [backup/restore design](BACKUP_RESTORE_DESIGN.md) and the [README](../README.md).
