# Engine service boundary

**Status:** Implemented on `dev`; CI status is tracked per commit in GitHub Actions  
**Scope:** One framework-independent signature-selection and rendering service shared by every transport adapter.

## Current implementation

The extraction is implemented in these components:

- `core/engine_contract.py` defines immutable `SignatureEngineRequest` and `SignatureEngineResult` objects, the shared sender/request normalizer and the transport-neutral `SignatureEngineError`.
- `core/engine_service.py` contains `SignatureEngineService.resolve()`. It owns domain and override policy, selector checksum validation, identity lookup, rule evaluation, group/variant selection, asset retrieval, sanitization and rendering.
- `main.py` provides `resolve_signature_engine()`, which constructs the service with the application-owned identity resolver and signature file store. The SQLAlchemy session is passed per call.
- The versioned HTTP endpoint and legacy Rspamd endpoint are thin wrappers around that same service.
- The Stalwart DATA hook authenticates its own Bearer token and calls the shared service directly. It no longer fabricates a FastAPI `Response`, calls the HTTP handler or passes the internal `ENGINE_API_KEY`.

The service module does not import FastAPI, inspect HTTP request/response objects, authenticate transport credentials or read process environment variables. Identity resolution and signature storage are constructor-injected dependencies.

## Contracts

`normalize_engine_request()` validates and normalizes the envelope sender and translates optional mode/selector inputs into an immutable request. Invalid sender addresses are rejected. An unsupported mode header intentionally falls back to automatic selection for compatibility; selector checksum validation remains in the service.

The result contract preserves the established response fields:

- `html`
- `inject_on_replies`
- `trim_whitespace`
- `strip_device_signatures`

`SignatureEngineResult.to_payload()` is the single JSON serialization definition for those fields. No adapter may create a second selection or rendering implementation.

## Behavior and persistence invariants

The extraction preserves the existing decision order and persistence boundaries:

1. Reject inactive or unknown domains and disabled user overrides with an empty result.
2. Validate body-marker selectors using the existing four-character SHA-256 checksum.
3. Prefer an exact-domain signature-name match; use a global signature only when no exact-domain match exists, and reject ambiguous matches.
4. Apply permitted user overrides unless an explicit mode was supplied.
5. Resolve explicit modes or evaluate persisted rules for automatic selection.
6. Use the existing group and variant selection repositories; keep rotation state in the caller's database transaction.
7. Sanitize stored HTML and nested group fragments before rendering; keep profile values autoescaped.
8. Commit rotation changes only after successful rendering, as before.

The selection repositories flush rotation updates but never commit them. By default, the service commits only after the complete signature (including nested group fragments) has rendered successfully. Callers may pass `commit_selection=False` to keep rotation updates pending while an adapter performs its final message mutation. The Stalwart hook uses this mode and commits only when it returns a `replaceContents` modification; signed, malformed, already-injected or otherwise unchanged messages roll back the pending rotation. On an exception, the caller must roll back or close the session before reusing it. Direct service tests cover render-failure rollback and deferred commit ownership.

Do not change selector precedence, sanitization, template behavior or commit timing as part of adapter work. Any future change to these invariants requires explicit regression tests.

## Transport boundaries

### HTTP engine endpoint

`GET /api/v1/engine/signatures/{url-encoded-envelope-sender}` validates `X-Engine-Key`, normalizes the request, calls the shared service and serializes the typed result. It returns `Cache-Control: no-store, private`. The legacy `GET /api/signature/{email}` route remains an alias for existing Rspamd installations.

Current typed engine error mapping:

| Engine error | HTTP status |
|---|---:|
| `INVALID_MODE` | 400 |
| `INVALID_STORED_RULE` | 500 |
| `INVALID_TEMPLATE` | 422 |
| Missing signature/version metadata or unavailable assets | 503 |

Malformed sender input is rejected with HTTP 400 before service invocation. Unexpected exceptions are not converted into empty signatures.

### Stalwart MTA hook

`POST /api/v1/adapters/stalwart/mta-hook` validates `STALWART_HOOK_TOKEN`, requires DATA-stage context, and obtains the sender from the SMTP envelope. It normalizes the same request contract and invokes `resolve_signature_engine()` directly, then translates the result into Stalwart message modifications.

The hook does not use `ENGINE_API_KEY`; its Bearer token authenticates the hook transport only. Presence of an authenticated SASL login is not proof that the principal may send as the envelope sender. Stalwart's SMTP authorization policy must enforce aliases and delegated send-as before the hook runs. Rotation changes remain uncommitted until MIME rewriting produces a `replaceContents` modification; otherwise the hook rolls them back.

Engine errors are translated through the same safe error mapping as the HTTP adapter. They must never be mistaken for an empty signature.

### Remote adapters

Rspamd and other separate-process/host adapters use the versioned HTTP endpoint and `X-Engine-Key`. In-process adapters call the shared service through the application boundary. Both paths must consume the same result contract and business logic.

## Remaining verification and release gates

- Keep direct service tests alongside HTTP-route and Stalwart contract tests.
- Verify parity for default templates, explicit modes, body markers, user overrides, persisted rules, group/variant selection, missing assets, template failures and transaction rollback.
- Run the complete CI suite, including static security checks, migrations, Docker smoke tests and encrypted backup/restore.
- Stalwart remains experimental until isolated SMTP/DKIM end-to-end tests cover the complete filter chain, sender spoofing/alias authorization, MIME edge cases, retries and duplicate prevention.
- Do not infer production readiness or deployment from CI success. No live deployment is part of this refactor.
