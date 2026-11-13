# Rich-text editor evaluation

**Decision:** Keep the currently vendored Quill 1.3.7 editor for now. Do not replace it with CKEditor 5 until an email-HTML compatibility proof of concept passes the acceptance tests below.

## Context

OpenSigWeave is licensed under GPL-3.0-or-later and is self-hosted. The editor must work without a third-party CDN at runtime, must produce predictable email-compatible HTML, and must not silently introduce premium-service dependencies or usage metering.

## Candidates

| Candidate | Licensing / distribution | Assessment |
|---|---|---|
| Quill 1.3.7 (current) | BSD-style permissive license; already vendored | Lowest migration risk. Current implementation is already integrated with the signature editor and CSP. Its email HTML output and table support still need explicit tests. |
| CKEditor 5 | Dual-licensed: GPL 2+ or commercial. The self-hosted GPL distribution is possible for GPL-compatible projects; commercial self-hosting requires a commercial agreement. GPL mode displays CKEditor's attribution/logo and excludes commercial-only features. | Technically attractive, but migration changes the editor integration and HTML output. GPL-2-or-later can be used in a GPL-3.0-or-later project, subject to preserving the applicable notices and complying with GPL terms. Do not use cloud CDN builds or premium features for the self-hosted preview. |
| TinyMCE | Multiple distribution and licensing options | Not selected for this pass; evaluate only if Quill and CKEditor fail the email-specific acceptance tests. |

## CKEditor 5 licensing notes

The official CKEditor documentation states that CKEditor 5 is dual-licensed under GPL 2+ or commercial terms. Self-hosted use under GPL is available when the integrating project complies with GPL requirements. The GPL license key is intended for the self-hosted distribution; cloud-hosted distribution requires a commercial license. The editor displays a “Powered by CKEditor” logo in the GPL configuration.

Official references:
- https://ckeditor.com/docs/ckeditor5/latest/getting-started/licensing/license-and-legal.html
- https://ckeditor.com/docs/ckeditor5/latest/getting-started/licensing/license-key-and-activation.html
- https://ckeditor.com/legal/ckeditor-licensing-options/

## Required proof-of-concept acceptance tests

Before replacing Quill, build a separate branch/fixture and test:

1. **Email HTML fidelity:** tables, inline styles, font sizes, colors, links, images, spacing, nested lists and signature layouts survive save/reload.
2. **No editor-only markup:** generated HTML does not require CKEditor runtime scripts, CSS classes or browser behavior to render in common email clients.
3. **Sanitizer compatibility:** the existing server-side sanitizer accepts the intended output without removing layout-critical styles or unsafe attributes.
4. **Outlook compatibility:** exported HTML renders acceptably in Outlook desktop (Word rendering engine), Outlook web and a standards-based client.
5. **Paste behavior:** content pasted from Microsoft Word and Outlook is normalized without importing unsafe markup or excessive styles.
6. **Security:** no arbitrary script, event handler, unsafe URL, iframe or active embedded content can be persisted.
7. **CSP and offline operation:** editor assets are vendored, version-pinned and work without external CDN requests.
8. **Accessibility and usability:** keyboard navigation, undo/redo, toolbar labels and focus handling work.
9. **License compliance:** retain the exact upstream license and copyright notices; include all required third-party notices in the distribution.
10. **Migration safety:** existing saved signature HTML remains byte-for-byte unchanged until a user explicitly edits and saves it.

## Next action

Create a CKEditor 5 GPL self-hosted proof of concept alongside the current editor, not as an in-place replacement. Compare the HTML output using the same fixture signatures and browser/email-client test matrix. If it offers no material improvement for email HTML, retain Quill rather than taking on a migration for editor features that do not improve the product.
