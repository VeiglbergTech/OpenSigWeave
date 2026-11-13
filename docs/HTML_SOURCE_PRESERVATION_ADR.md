# HTML source preservation and safe signature rendering

- **Status:** Accepted design; implementation pending
- **Scope:** Personal overrides, domain templates, administrator overrides, versioned signatures
- **Related:** `signature_sanitizer.py`, `signature_renderer.py`, `signature_catalog.py`, `signature_storage.py`

## Context

OpenSigWeave currently sanitizes signature HTML before persistence in all four storage paths:

| Content type | Current write path | Current storage |
|---|---|---|
| Domain template | `PUT /domains/{domain_id}` | `domains.template_html` |
| Personal override | `POST /api/my-signature` | `user_overrides.html_content` |
| Administrator override | `POST /api/admin/overrides` | `user_overrides.html_content` |
| Reusable signature/version | `create_signature` / `add_signature_version` | `signatur/{name}/.versions/{id}.html` |

The sanitizer intentionally removes comments and unsupported elements, including Outlook conditional comments and VML. Because sanitization happens before persistence, the original source cannot be recovered. A client-side warning alone does not solve this data-loss problem.

## Decision

Separate **editable source HTML** from **renderable HTML**.

1. Persist the exact submitted source HTML (subject to input type and size validation) as the editor's source of truth.
2. Continue to derive renderable HTML with the existing allowlist sanitizer. Never broaden the allowlist as a side effect of source preservation.
3. Sanitize immediately before template rendering, including all nested/group templates and preview paths.
4. Only sanitized, rendered HTML may leave the signature rendering boundary for mail injection.
5. Raw source may be returned only by authenticated editor/catalog APIs that already authorize the corresponding user or administrator. It must never be included in the Rspamd response or an unauthenticated endpoint.
6. Keep the warning in the editor, but change its meaning after rollout: Outlook conditional markup is retained in source mode but is excluded from sanitized output. Do not claim that preserving source makes Outlook VML render in delivered messages.

This deliberately separates fidelity of stored source from the security policy of delivered HTML. It does **not** promise that unsupported markup will appear in outgoing email.

## Storage model

### Legacy database-backed content

Add nullable source columns:

- `domains.template_html_source`
- `user_overrides.html_content_source`

Keep the existing `template_html` and `html_content` columns as sanitized renderable snapshots for compatibility with existing delivery queries and integrations.

On every write, in one database transaction:

- validate the original input and enforce the existing 64 KiB limit;
- store the exact input in the corresponding `*_source` column;
- store `sanitize_signature_html(source)` in the existing renderable column.

Editor GET/list APIs return the source column (falling back to the renderable column for pre-migration or partially migrated rows). Delivery continues to read only the renderable column and sanitizes it again at the rendering boundary as defense in depth.

The migration backfills each source column from its existing sanitized column. This cannot restore historical Outlook markup that has already been discarded; it only establishes a consistent source field for future edits.

### Versioned reusable signatures

Version files become immutable **source snapshots**. `signature_versions.sha256` and `size_bytes` describe the exact source bytes, not sanitized output. The current `signature.html` convenience file mirrors the source of the latest version.

Remove pre-persistence sanitization from `signature_catalog.py`. Keep storage integrity checks over the original bytes. Sanitize after reading a version and immediately before preview or mail rendering. Existing versions are already sanitized; they remain valid source snapshots and need no destructive rewrite.

No second sanitized asset is required: sanitized HTML is deterministic derived data and should be generated at the trust boundary. If performance later justifies caching it, the cache must be disposable and keyed by the source SHA-256 plus sanitizer-policy version.

## Rendering and trust boundaries

The intended data flow is:

```text
Editor input
    |
    v
Validate type and size
    |
    +----> Persist original source (database source column / immutable version file)
    |
    v
Allowlist sanitizer
    |
    v
Persist sanitized snapshot (legacy database columns only)
    |
    v
Read sanitized content for delivery
    |
    v
Sanitize again -> sandboxed template rendering -> sanitized group expansion
    |
    v
Rspamd response / mail injection
```

Security invariants:

- The raw source is never passed directly to `Markup`, `srcdoc`, the Rspamd response, or a mail client.
- Dynamic identity values remain HTML-escaped by the sandboxed renderer.
- Group template content is sanitized before it is wrapped as `Markup`.
- Nested group expansion remains one level deep.
- Sanitization remains allowlist-based; no blanket VML, comment, `style`, event-handler, or arbitrary-tag allowance is introduced.
- File size, database request limits, file permissions, path checks and SHA-256 integrity validation remain in force.
- Preview and delivery use the same sanitizer/rendering policy. A preview must not be more permissive than actual mail output.

## API and editor contract

| Surface | Read for editor | Write | Delivery source |
|---|---|---|---|
| Personal override | `html_content_source` with legacy fallback | source + sanitized snapshot | `html_content` |
| Domain template | `template_html_source` with legacy fallback | source + sanitized snapshot | `template_html` |
| Administrator override | `html_content_source` with legacy fallback | source + sanitized snapshot | `html_content` |
| Reusable signature version | immutable version source file | immutable source version | source file, sanitized after read |

All editor-facing routes must preserve their existing authorization checks. The internal mail-rendering endpoint must return only the sanitized rendered result.

## Migration and rollout

1. Add the two nullable source columns and backfill from current sanitized values.
2. Update personal, domain and administrator write/read endpoints. Add tests proving source round-trip and sanitized delivery behavior.
3. Change reusable signature version writes to store source bytes; retain SHA-256 verification against those bytes. Add tests for exact source round-trip and sanitizer-at-render behavior.
4. Audit every preview, nested group, legacy fallback and mail-rendering call site. Ensure no path skips sanitization and no editor API is used by the mail injection client.
5. Update the editor warning text and add browser-level tests for all three raw HTML editors.
6. Only then consider any narrowly scoped Outlook output support as a separate security review and compatibility decision.

The migration must not attempt to reconstruct source markup from old sanitized records. A downgrade must not silently discard newly saved source HTML; if a downgrade path cannot preserve it, it must be explicitly blocked or preceded by an export/backup.

## Acceptance criteria

- Source HTML round-trips byte-for-byte (UTF-8 text) through save and editor reload for all four content types.
- Sanitized HTML continues to remove scripts, event handlers, unsafe URL schemes, comments and disallowed VML.
- The mail-rendering endpoint and group preview return sanitized, rendered HTML only.
- Dynamic template values remain escaped.
- Group selection, version history, file integrity checks and backup/restore remain intact.
- Existing records migrate without data loss; historical content already removed by the old sanitizer is not falsely represented as recoverable.
- All source-returning routes retain their existing authorization boundaries.
