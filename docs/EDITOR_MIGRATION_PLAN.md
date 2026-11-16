# CKEditor 5 integration and acceptance plan

The self-hosted CKEditor 5 integration is implemented on the `dev` branch. The previous Quill-based migration proposal is superseded by [EDITOR_DECISION.md](EDITOR_DECISION.md).

## Current implementation

- CKEditor 5 is pinned to 49.1.0.
- Docker builds the browser bundle in a separate Node 22 Alpine stage using esbuild.
- JavaScript, CSS and the upstream license are copied into the Python application image and served locally.
- The shared editor adapter keeps the visible editor and raw HTML textarea synchronized.
- Personal signature, signature template, domain master template and domain user override surfaces use the adapter.
- Raw HTML remains available; backend sanitization is authoritative.
- The toolbar provides common email-signature formatting, tables and links.
- Existing table-heavy layouts in selected override surfaces are locked to prevent destructive round trips; Outlook-specific conditional comments and VML trigger warnings.

## Required verification

1. Build the complete Docker image and verify the CKEditor JavaScript, CSS and license assets are present.
2. Run the browser smoke test: editor loads without a CDN, formatting works, source HTML synchronizes, and signature create/update succeeds.
3. Exercise personal, domain master-template and per-user override editor surfaces.
4. Test representative simple HTML, nested tables, inline styles, links, images, Outlook conditional comments and VML against the backend sanitizer.
5. Verify signature group token insertion at the editor cursor.
6. Confirm CSP remains restrictive; do not add broad inline-script or inline-style allowances to make the editor work.
7. Verify the GPL license and bundled notices remain in the image.

The browser smoke test and CI are necessary but do not replace real-world rendering checks in Outlook, Gmail and mobile clients. Do not connect production mail flow solely because the editor passes browser tests.
