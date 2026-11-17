# Engine service boundary

**Status:** In progress — typed request/result/error contracts implemented; service extraction remains open  
**Scope:** Extraction of signature selection and rendering from the FastAPI route so every mail adapter uses one application service.

## Current implementation

`core/engine_contract.py` now defines immutable `SignatureEngineRequest` and `SignatureEngineResult` contracts plus a transport-neutral `SignatureEngineError` carrying a stable error code and safe message. The HTTP handler uses the request normalizer and serializes both empty and rendered results through `SignatureEngineResult`, while preserving the existing JSON response fields.

The versioned endpoint `GET /api/v1/engine/signatures/{email}` and the legacy Rspamd alias still share the FastAPI handler `get_engine_signature`. The Stalwart adapter still calls that handler directly in-process and supplies a synthetic `Response`, the configured engine key and the SQLAlchemy session. The selection, rule evaluation, rotation, storage and rendering logic therefore remains in `main.py`; the typed error contract is defined but not yet used for service-level error mapping.

This is an interim arrangement: an adapter is coupled to the HTTP handler's parameter order, FastAPI response type and API-key check. The Stalwart route must know the internal `ENGINE_API_KEY` even though it has already authenticated its own Bearer token. The remaining extraction must remove that coupling without duplicating business logic.

## Target responsibilities

### Core service

A framework-independent `SignatureEngineService` owns:

- sender normalization and validation;
- domain and override policy;
- selector validation and precedence;
- identity attribute resolution;
- rule evaluation and group/variant selection;
- signature asset retrieval, sanitization and rendering;
- rotation-state persistence;
- the stable result fields consumed by adapters.

The service MUST NOT read HTTP headers, inspect FastAPI `Request`/`Response` objects, authenticate transport credentials, or read process environment variables directly.

### Request and result contracts

Use typed immutable request/result objects. The request should carry the normalized envelope sender and optional selection inputs (mode, signature name and selector checksum). The result should carry the rendered HTML and the three existing policy flags: `inject_on_replies`, `trim_whitespace` and `strip_device_signatures`.

Do not silently change selector precedence or response semantics during extraction. In particular, preserve the current checksum validation, exact-domain-before-global signature-name matching, override behavior, explicit mode behavior, rule evaluation and deterministic group/variant selection.

### Dependency boundary

Inject the dependencies the service currently obtains from module globals:

- SQLAlchemy session / repository boundary;
- asynchronous identity-attribute resolver;
- signature file store.

The service may use existing domain models, rule evaluators, selection repositories, sanitizer and renderer. Avoid introducing a generic plugin framework or duplicating these collaborators merely to complete the extraction.

### Error contract

Replace service-level `HTTPException` usage with a small typed engine error carrying a stable internal error code and safe message. The HTTP route maps those errors to the existing status codes (400, 422, 503). Stalwart maps them to its documented hook failure policy; it must not mistake an engine error for an empty signature.

Unexpected exceptions remain unexpected and must be logged with a correlation-safe context. Do not expose database, filesystem or template internals to the mail server.

## Adapter boundaries after extraction

- **HTTP engine endpoint:** validates `X-Engine-Key`, builds the request, invokes the service and serializes the result. The legacy route remains an alias.
- **Stalwart hook:** validates its Bearer token, validates DATA-stage context, obtains the sender from the SMTP envelope, extracts selectors, invokes the same service directly, then translates the result to Stalwart modifications. It must not pass `ENGINE_API_KEY` or fabricate a FastAPI `Response`.
- **Rspamd and remote adapters:** continue using the versioned HTTP contract and `X-Engine-Key`; no adapter-specific selection/rendering implementation is permitted.

Transport authentication remains independent: authenticating a Stalwart hook request does not prove that the SASL principal may send as the envelope sender. Stalwart's SMTP authorization policy must enforce aliases and delegated send-as before the hook runs.

## Safe extraction sequence

1. Add request/result/error types and unit tests for normalization and selector input validation. No behavior change.
2. Move the existing selection/rendering body into the service with injected collaborators. Preserve branch order and database commit timing.
3. Make the versioned and legacy HTTP routes thin authentication/serialization wrappers. Keep the exact existing response schema and status mapping.
4. Change Stalwart to call the service directly; remove the synthetic `Response` and internal engine-key argument.
5. Add service-level parity tests and route/adapter contract tests. Assert both transports receive identical HTML and policy flags for the same database state and request.
6. Run the complete CI suite. Only after all checks pass should the adapter documentation be updated to mark the extraction complete.

## Release gates

- Existing API and Rspamd compatibility tests pass unchanged or with equivalent explicit contract assertions.
- Stalwart does not depend on FastAPI handler signatures or the engine HTTP credential.
- Selection, rendering, error semantics and rotation persistence are identical across both paths.
- No changes to `beta` or `main` as part of this refactor; no live deployment is implied by CI success.
