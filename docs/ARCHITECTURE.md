# OpenSigWeave – technical architecture

**Status:** Proposed target architecture  
**Decision baseline:** 2026-11-10  
**Implementation branch:** `architecture/refactor-foundation`

## 1. Goals

OpenSigWeave is a self-hosted, mail-gateway signature management system. Its core must remain independent of any single mail server or identity vendor. Mailcow/Rspamd is the first reference integration.

Primary capabilities:
- Central management of domains, signatures, variants and rules.
- Per-message signature resolution at the mail gateway.
- Random selection among eligible variants without immediately repeating the previous variant for a sender.
- Explicit modes: `AUTO`, `PROFESSIONAL`, `INTERNAL`, `NONE`.
- Identity-aware rules based on sender, domain, OIDC claims and (later) directory attributes.
- Hybrid storage: versioned HTML files plus PostgreSQL metadata.
- OIDC-based web authentication.
- Auditable administrative changes.

Non-goals for the first implementation:
- Replacing a mail server or filtering engine.
- Requiring a specific identity provider.
- Modifying the production mail system during development.
- Guaranteeing deliverability without integration evidence.

## 2. Logical components

### Web/API layer
FastAPI routers expose browser and engine APIs. Routers validate input and enforce authorization but do not contain signature selection logic.

### Identity and authorization
OIDC handles interactive login using discovery metadata. A normalized principal contains subject, issuer, email and claims. Authorization maps configured roles/groups to application permissions.

An independent attribute-provider interface supplies employee attributes to templates and rules. OIDC claims are the initial provider. LDAP/AD enrichment can be added without changing the signature core.

### Signature core
Pure application services:
- `ModeResolver`: validates and resolves requested mode.
- `RuleEngine`: evaluates prioritized conditions.
- `VariantSelector`: chooses an eligible variant.
- `SignatureRenderer`: renders approved HTML using normalized identity attributes.
- `SignatureService`: orchestrates resolution without knowing the mail platform.

### Persistence
PostgreSQL stores relational metadata, rules, selection state, audit records and version manifests. Alembic owns schema evolution.

### File storage
A configured signature root contains immutable or versioned HTML assets. Each version has a generated identifier, relative path and SHA-256 hash. The application validates paths remain beneath the configured root. Writes use temporary files followed by atomic rename; metadata activation occurs transactionally after file persistence succeeds.

### Mail adapter
The Rspamd adapter extracts trusted sender context, reads the supported mode marker/header, calls the internal engine API and applies the returned signature. Rspamd-specific behavior remains isolated from the core.

## 3. Resolution order

The default resolver uses explicit numeric priority, not incidental database ordering. Suggested default precedence:

1. Explicit valid message mode (`PROFESSIONAL`, `INTERNAL`, `NONE`).
2. Exact sender override.
3. Matching identity/attribute/group rules.
4. Domain rules.
5. Global default.

A mode can either select a named signature pool or short-circuit injection. Invalid or unauthorized mode requests fall back according to a documented policy; they must never grant elevated behavior.

## 4. No-immediate-repeat selection

Selection state is scoped by a stable sender key and selection pool. The database transaction must serialize concurrent selection for the same key (for example, row-level locking or an atomic update strategy). Candidate selection excludes the last selected variant when at least two eligible variants exist. If only one candidate exists, it remains selectable.

Persist the selected variant and update timestamp in the same transaction. Define behavior for deleted/disabled variants and for a changed pool membership.

## 5. Initial relational model

Proposed tables (names are provisional):

- `domains`: normalized domain name, status and policy.
- `principals`: optional cached identity subject/email and source issuer.
- `signatures`: logical signature identity and scope.
- `signature_versions`: immutable version metadata, relative file path, SHA-256, author and creation time.
- `signature_variants`: eligible variant membership, mode/pool, weight and status.
- `rules`: priority, enabled state, target action and scope.
- `rule_conditions`: typed conditions (sender, domain, claim, group).
- `user_overrides`: exact sender-specific behavior, including disable state.
- `selection_state`: last selected variant per sender and pool.

The current branch now contains initial SQLAlchemy models and an Alembic migration for `signatures`, `signature_versions`, `signature_variants` and `selection_state`. The filesystem component writes UUID-named immutable HTML files and verifies SHA-256 hashes. The transaction-aware selection repository now performs conflict-safe state creation and row locking for each sender/pool. Integration with the message API and PostgreSQL concurrency testing remain.
- `audit_logs`: actor, action, target, timestamp and redacted change summary.

Use foreign keys, uniqueness constraints, indexes for normalized email/domain lookup and check constraints for valid modes/weights. Avoid storing unnecessary personal data; define retention for audit and cached identity attributes.

## 6. API boundaries

Initial internal engine contract (illustrative; exact schema to be versioned before implementation):

Request:
- `sender`: normalized envelope sender.
- `recipient_context`: optional trusted recipient/domain context if required by policy.
- `mode`: optional validated mode.
- `message_id`: optional correlation identifier; do not use it as an authorization credential.
- `identity_context`: server-resolved identity claims only; never trust arbitrary client-supplied claims.

Response:
- `signature_html`: rendered HTML or empty result.
- `signature_version_id`: selected immutable version identifier.
- `policy`: only the mail-adapter flags it needs.
- `decision_id`: correlation/audit identifier.

The engine endpoint authenticates the Rspamd adapter with a rotatable secret or stronger service authentication. Do not put credentials in source code or frontend assets. Avoid sending full message bodies to the API.

## 7. Mail processing and DKIM

Rspamd integration must be verified against the actual deployed Rspamd version and effective configuration. The test must demonstrate:
- Signature insertion occurs before DKIM signing.
- The final delivered message has a valid DKIM signature.
- No mutation occurs after signing.
- Repeated processing is idempotent.
- Multipart/alternative, nested multipart, attachments, HTML, plain text, quoted-printable and base64 fixtures remain valid.

Do not treat the current Lua `prefilter` registration alone as proof of DKIM order.

## 8. Operational requirements

- Non-root application container/process.
- PostgreSQL credentials and engine secrets supplied through protected runtime secrets.
- Mandatory strong session secret; secure cookies behind HTTPS.
- TLS verification enabled by default.
- Explicit health/readiness checks.
- Structured logs with no signature HTML, tokens or unnecessary personal data.
- Bounded HTTP timeouts and documented fail-open/fail-closed behavior.
- Backups for PostgreSQL and signature assets; restore procedure must preserve version hashes.
- Migrations run as a controlled deployment step, not implicitly at module import.

## 9. Compatibility and migration strategy

Refactor incrementally:
1. Add tests and record current API/UI behavior.
2. Extract configuration/database modules while preserving SQLite only as a temporary migration bridge if needed; PostgreSQL remains the target.
3. Introduce Alembic and migrate existing domain/override data.
4. Extract core services behind tests.
5. Add new variant/rule tables and migrate existing domain templates/overrides into a default variant model.
6. Replace the Authentik-specific integration with OIDC plus attribute-provider interfaces.
7. Replace the Rspamd hard-coded configuration and version the internal API.
8. Keep old API routes temporarily as compatibility wrappers where safe, then deprecate them.

## 10. Open design questions

Resolve these before implementing the corresponding feature:
- Which OIDC claims are guaranteed by the provider, and which user attributes require directory enrichment?
- Should `PROFESSIONAL` and `INTERNAL` be fixed signature pools or explicit rule targets?
- What exact message marker formats survive Outlook, mobile clients and reply/forward composition?
- Which sender identity is authoritative: SMTP envelope sender, authenticated user or message From? Define trusted precedence.
- What is the default behavior when the engine is unavailable?
- What HTML sanitization policy preserves email-client compatibility while protecting the management UI?
- How should multiple recipients from different domains affect a single outgoing message?

These are implementation gates, not reasons to block documentation and baseline test work.
