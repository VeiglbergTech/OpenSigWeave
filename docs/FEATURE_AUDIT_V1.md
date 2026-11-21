# OpenSigWeave v1.0 feature audit

Baseline: `dev` commit `e8bdac7e8d5f`, 20 November 2026.

Categories: **1** = must be available in Web UI; **2** = deliberately
operator/API/CLI-only; **3** = internal processing, not a user control.

| Feature | Category | Web | API | CLI | Rationale |
|---|---:|---|---|---|---|
| Authentik, OIDC and LDAP provider CRUD, enable/disable, connection test | 1 | Yes | Yes | No | Provider management |
| Per-domain login and profile provider assignment | 1 | Yes | Yes | No | Independent identity roles |
| Local administrator and SSO browser login | 1 | Yes | Browser routes | No | Human authentication |
| Domains: CRUD, activation, defaults and reply policy | 1 | Yes | Yes | No | Routine operations |
| Signature catalog: create, edit, scope and disable | 1 | Yes | Yes | No | No destructive historical purge |
| CKEditor, image upload, previews | 1 | Yes | Yes | No | Persistent signature assets |
| Signature versions, historical preview and restore-as-new | 1 | Yes | Yes | No | Preserve history |
| Groups, membership weights and preview | 1 | Yes | Yes | No | Weighted selection management |
| Rules, conditions, targets and priorities | 1 | Yes | Yes | No | Business policy |
| Schedules, UTC windows, targets, domain and priority | 1 | Yes | Yes | No | Calendar-based assignment |
| Branding name, color, logo, favicon and reset | 1 | Yes | Yes | No | Normal administrator settings |
| Personal signature overrides | 1 | Yes | Session API | No | Identity-scoped |
| Per-user admin overrides / locking | 1 | Yes | Yes | No | Domain Users |
| Selective configuration ZIP export | 1 | Yes | Yes | No | Excludes secrets |
| Additive import with preview and conflict validation | 1 | Yes | Yes | No | Guarded browser import |
| Read-only application/DB/storage status and record counts | 1 | **Added** | **Added** | No | Bounded admin diagnostics |
| User-directory account lifecycle | 2 | No | External IdP | External IdP | AD/LDAP/OIDC owns identities |
| Full encrypted backup and fresh-host restore | 2 | No | No | Yes | Destructive, privileged Docker/filesystem operations |
| Archive package, verify, decrypt and prune | 2 | No | No | Yes | Local backup CLI |
| Scheduled backup timer and mounted storage | 2 | No | No | script/systemd | Infrastructure control |
| Bootstrap password, session and engine secrets | 2 | No | No | env/script | Do not expose keys in browser |
| Optional machine management credential | 2 | No | Yes | env | Full admin scope, operator secret |
| Host networking, SMTP/DKIM, TLS and adapter configuration | 2 | No | Deployment | operator | Mail-server responsibility |
| Docker, adapter and MTA log/queue diagnostics | 2 | No | No | operator | No remote command execution |
| Engine signature resolution | 3 | No | Adapter contract | No | Authenticated transport endpoint |
| Stalwart hook and Rspamd/Postfix MIME handling | 3 | No | Adapter-only | No | Transport implementation |
| Rule/schedule evaluation and rotation state | 3 | No | Internal | No | Engine implementation |
| Identity cache, HTML sanitation and version integrity | 3 | No | Internal | No | Security/data invariants |

## Finding and boundaries

All existing normal administrator management endpoints have corresponding
Web UI actions. The clear gap was system-level visibility: a limited
`GET /api/admin/system-status` and read-only **System** tab are now added.
No migrations, root access, shell execution, remote backup/restore, key editor
or duplicate CLI were introduced. User management remains in the external IdP;
OpenSigWeave handles per-user overrides only.

Targeted tests verify unauthorized access, management-key handling,
no secret disclosure, degraded storage reporting, UI rendering and refresh.
CI must be checked for the resulting `dev` revision. Real SSO, DKIM/SMTP
and cross-host recovery still require deployment acceptance. `beta` and
`main` must remain unchanged during this audit.
