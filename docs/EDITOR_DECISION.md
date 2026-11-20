# Rich-text editor decision

**Decision:** Use the self-hosted CKEditor 5 GPL build for the portal's rich-text editing surfaces.

The application pins CKEditor 5 to **49.1.0**. The Dockerfile builds the browser bundle in a Node 22 Alpine stage with esbuild and copies the generated JavaScript, CSS and license into the application image. CKEditor is served locally from `/static/vendor/ckeditor5/49.1.0/`; there is no runtime dependency on a CKEditor CDN or cloud service.

The shared `OpenSigWeaveEditor` adapter is used by:
- The user's personal signature editor.
- The administrator's signature template editor.
- Domain master-template and user-override editors.

The toolbar includes headings, font family/size/color, bold/italic/underline, links, lists, alignment, tables, block quotes and horizontal rules. General HTML Support is configured for the email-oriented elements and attributes used by signatures. The source HTML textarea remains available as an advanced fallback; backend sanitization remains authoritative.

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

Future editor changes must preserve offline operation, email-compatible HTML, source round trips, sanitizer behavior, Outlook warnings and all three editor surfaces. Any CKEditor upgrade must be version-pinned and pass the full CI and real-browser acceptance suite before beta deployment.
