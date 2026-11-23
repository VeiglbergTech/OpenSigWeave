# Rich-text editor decision

**Decision:** Use the self-hosted CKEditor 5 GPL build for the portal's rich-text editing surfaces.

## Unified integration (dev)

All active rich-text surfaces (personal signature, administrator override, versioned
signature catalog) use `OpenSigWeaveEditor.create` from `static/js/rich-editor.js`.
The wrapper owns one shared plugin list, toolbar layout, font command policy,
font family list, CKEditor-native font dropdowns, theme styles and HTML support configuration.
The font family dropdown and combined font-size dropdown are CKEditor component-factory
views in the editor toolbar, not an additional DOM toolbar. Font sizes offer
8/9/10/11/12/14/16/18/20/24/28/32/36 pt presets plus a validated entry for
6–96 pt (or explicit legacy px) including decimal values such as 17,5 pt.
Unmodified legacy px and arbitrary supported inline sizes are preserved; newly
selected sizes serialize to inline CSS in pt. Both dropdowns track the selection.
`portal.js` only binds each editor to its authorized source textarea and workflow;
it must not assemble independent CKEditor configurations.

Catalog-only image upload and HTTPS URL import remain explicitly permission-scoped:
they use the administrator's validated asset endpoints, so do not expose a broken or
unauthorized upload action in personal overrides. The rest of the editor toolbar
and font styling are identical. The former domain-master editor is retired.

**Source fidelity:** `getData()` returns the exact source provided at initialization
or `setData()` until a real CKEditor model edit changes its normalized content.
Programmatic loading does not invoke `onChange` or replace the raw textarea.
After edits, CKEditor's serialization is the new editable source. Raw HTML mode
remains the required path for unsupported comments, VML, and exotic Outlook
markup. Rendering and mail output still pass through the server sanitizer,
which intentionally strips unsupported Outlook/VML content. The design does
not promise byte-for-byte HTML preservation *after* editing visually, nor that
raw Outlook VML survives sanitization into delivered email.

The application pins CKEditor 5 to **49.1.0**. The Dockerfile builds the browser bundle in a Node 22 Alpine stage with esbuild and copies the generated JavaScript, CSS and license into the application image. CKEditor is served locally from `/static/vendor/ckeditor5/49.1.0/`; there is no runtime dependency on a CKEditor CDN or cloud service.

The shared `OpenSigWeaveEditor` adapter is used by:
- The user's personal signature editor.
- The administrator's signature template editor.
- Administrator user-override editors. The retired domain master-template editor is no longer available in the portal.

The toolbar includes headings, font family/size/color, bold/italic/underline, links, lists, alignment, tables, block quotes and horizontal rules. The GPL-compatible CKEditor image-resize plugin provides drag handles and image toolbar width presets plus custom sizing. General HTML Support is configured for the email-oriented elements and attributes used by signatures. The source HTML textarea remains available as an advanced fallback; backend sanitization remains authoritative.

Signature image handling:
- The administrator signature-template editor supports PNG, JPEG and WebP through file drag & drop, clipboard image paste and the CKEditor upload-image control. Existing GIF upload compatibility remains supported.
- All editor upload paths use the same authenticated, CSRF-protected OpenSigWeave upload endpoint. The server validates the declared MIME type against the file signature and stores the image under a SHA-256 content-addressed immutable filename.
- CKEditor persists an absolute OpenSigWeave `/signature-assets/` URL in signature HTML. The public asset route intentionally requires no portal session so mail clients can retrieve the image, and serves immutable cache headers.
- Signature image files are independent from individual HTML versions and signatures. Removing an `<img>` reference does not delete the underlying shared asset, so older versions and other signatures keep working.
- Persisted signature HTML must not contain Base64 `data:` images or temporary `blob:` URLs.

Compatibility safeguards:
- Existing advanced/table-based layouts in the personal and domain override editors are visually locked to avoid accidental data loss.
- Outlook conditional comments and VML are detected and warned about; users must retain and review the raw HTML source for Outlook-specific markup.
- The editor must never be treated as a sanitizer or security boundary.
- Signature group tokens can be inserted at the current editor selection.
- Browser smoke tests verify self-hosted asset loading, formatting, synchronization to source HTML and personal/domain/administrator/signature-catalog editing under the current report-only Content Security Policy. They also exercise real CKEditor image drag & drop, clipboard paste, the upload-image control, save/reopen persistence, anonymous asset retrieval, shared references and historical versions. The same Docker/Chromium flow also exercises Scheduling and Branding administration.

The distribution is licensed under GPL 2+ and is compatible with OpenSigWeave's GPLv3 distribution. Keep the exact CKEditor license file in the image and repository notices.

Future editor changes must preserve offline operation, email-compatible HTML, source round trips, sanitizer behavior, Outlook warnings and all supported editor surfaces. Any CKEditor upgrade must be version-pinned and pass the full CI and real-browser acceptance suite before beta deployment.
