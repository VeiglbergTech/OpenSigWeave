# Repository and security audit

**Baseline:** beta/release-candidate source tree  
**Review date:** 2026-11-19  
**Scope:** code/repository review plus automated CI evidence. This is not a penetration test or production security certification.

## Release hygiene

The repository is organized around a standalone Docker deployment. The deployment source of truth is the `beta` branch; `dev` is not intended as an operator deployment source.

Release-candidate cleanup removed obsolete branding placeholder files and the superseded Quill-to-CKEditor migration plan. The bundled `branding/logo.png` is tracked and explicitly included in the Docker build context.

The CKEditor build stage uses an exact Node/Alpine image version. Runtime Python and PostgreSQL images remain pinned as defined by the Docker/Compose files and are tracked by dependency automation.

## Automated security controls

The application workflow checks each candidate for:

- secret leaks across complete Git history with Gitleaks;
- vulnerable Python dependencies with `pip-audit`;
- Python static security findings with Bandit;
- Python and browser JavaScript syntax;
- migration compatibility on PostgreSQL and SQLite;
- Docker build/startup and browser workflows;
- report-only CSP violation events in Chromium;
- encrypted full backup/fresh restore;
- regression tests.

Dedicated Postfix CI separately exercises PyMilter/libmilter and disposable Postfix.

Historical Gitleaks allowlist entries are retained where required for old Git objects. Removing an allowlist entry merely because the current tree no longer contains that artifact would break complete-history scanning without removing the historical object.

## Manual review highlights

The current source review specifically checked common high-risk patterns and configuration boundaries. No new obvious code-level vulnerability requiring a release block was identified in the reviewed tree. This statement is deliberately narrower than claiming the application is vulnerability-free.

Controls observed in the current implementation include:

- no production fallback for required session/engine secrets;
- minimum-length and known-placeholder rejection for sensitive keys;
- CSRF protection on state-changing browser requests;
- explicit trusted-host validation;
- Secure/HttpOnly/SameSite session cookies;
- database-backed rate limits and bounded request bodies;
- forwarded-client-IP trust only from configured proxy CIDRs;
- HTTPS/LDAPS certificate validation;
- server-side signature HTML sanitization and autoescaped dynamic values;
- sandboxed previews;
- file-store path/symlink protection, atomic writes and SHA-256 integrity checks;
- non-root/read-only application container with dropped capabilities and `no-new-privileges`;
- PostgreSQL not published by Compose;
- bounded adapter message/header/HTTP-response handling.

## Release-candidate issues found and fixed

The audit did find concrete problems before beta promotion:

1. **Bundled logo excluded from Docker build context.** The tracked default logo was ignored by `.dockerignore`, so a deployment could start without the expected product asset. The ignore rule was removed and CI now verifies the logo inside the built container and through the public branding endpoint.
2. **Branding request-envelope size mismatch.** Raw logo/favicon limits were safe individually, but base64 JSON overhead could collide with the generic 1 MiB body limit. The branding route now has a separate bounded 2 MiB request budget while binary asset limits remain 512 KiB/256 KiB.
3. **Mutable Node builder tag.** The CKEditor build stage used `node:22-alpine`; it is now pinned to an exact Node/Alpine version.
4. **Scheduling UI stale catalog state.** A signature created after page initialization did not immediately appear in Scheduling. Scheduling now refreshes when signatures, groups or domains change, and CI exercises this real workflow.
5. **Coverage gap for new administration features.** Browser CI now creates/updates/deletes a schedule and uploads/resets branding assets instead of merely checking that the tabs render.

## Known security/operational limitations

- CSP remains `Content-Security-Policy-Report-Only`. Current CI records no violation events on exercised paths, but broader browser/deployment acceptance is required before enforcement.
- Starlette's signed client-side session cookie is not a centrally revocable server-side session. Logout removes the browser cookie, but a copied valid cookie can remain valid until expiry.
- Generic OIDC cannot provide a portable immediate account-revocation check; verified claims/attributes may remain cached according to the implementation policy.
- Adapter transport authentication does not replace MTA sender authorization. The mail platform must enforce that an authenticated principal may use the envelope sender.
- Exactly-once processing across independent re-submissions is not guaranteed.
- Real MTA/DKIM, client-rendering, cross-host DR and load testing remain acceptance gates.

## Review policy

Do not weaken security controls merely to make a deployment or client work. In particular:

- do not disable TLS certificate verification;
- do not add wildcard trusted hosts;
- do not trust client-supplied OpenSigWeave processing headers/comments as idempotency proof;
- do not reconstruct multipart MIME with regular expressions;
- do not broaden CSP with `unsafe-inline`/`unsafe-eval` without a specific reviewed requirement;
- do not place secrets in Compose files, source code or tracked adapter examples.

Current operational status and remaining acceptance work are tracked in [IMPLEMENTATION_PROGRESS.md](IMPLEMENTATION_PROGRESS.md).
