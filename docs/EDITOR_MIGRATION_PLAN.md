# Editor migration assessment

## Decision

Evaluate CKEditor 5 as the next rich-text editor, but do not replace Quill until the HTML round-trip tests pass. The application stores and processes HTML email signatures, so preserving existing markup is more important than matching a generic document-editor feature list.

The repository is licensed under GPL-3.0. CKEditor 5's self-hosted open-source distribution is licensed under GPL 2+, which can be used in a GPL-3.0 project. The GPL distribution must be self-hosted (npm or ZIP) and configured with `licenseKey: 'GPL'`; it displays the CKEditor attribution in the editor. Do not use the CDN distribution with the GPL key.

Official references:
- [CKEditor 5 license and legal terms](https://ckeditor.com/docs/ckeditor5/latest/getting-started/licensing/license-and-legal.html)
- [CKEditor 5 license key and activation](https://ckeditor.com/docs/ckeditor5/latest/getting-started/licensing/license-key-and-activation.html)
- [General HTML Support](https://ckeditor.com/docs/ckeditor5/latest/features/html/general-html-support.html)
- [Export with inline styles](https://ckeditor.com/docs/ckeditor5/latest/features/export-with-inline-styles.html)

## Current Quill integration

Quill 1.3.7 is vendored locally and used in three places:

1. Personal signature editor.
2. Domain default-template editor.
3. Administrator user-override editor.

The personal editor toolbar currently provides bold, italic, underline, text/background colors, ordered and unordered lists, links, images and clear formatting. The domain and administrator editors provide bold, italic, underline, text/background colors, links, images and clear formatting.

The rich-text editors synchronize their HTML into separate raw HTML textareas. Save handlers read the textarea values. Sanitizer/import actions also update the raw textarea and then load the same HTML into Quill. The domain and administrator editors are initialized lazily when their modal opens.

This means the raw HTML textarea is currently the source of truth for persistence. The editor is a convenience layer, not the storage format.

## CKEditor 5 GPL fit

The GPL distribution is a viable candidate for this GPL-3.0 repository, provided it is self-hosted and only GPL-available features are used.

Potential benefits:
- Dedicated table editing and table properties.
- More complete formatting and list controls.
- General HTML Support (GHS) can retain explicitly allowed elements, attributes, classes and styles.
- Better foundation for a richer editing experience than the current Quill 1.3.7 integration.

Important constraints:
- GHS preserves configured markup; it does not provide a complete editing UI for every arbitrary HTML construct.
- Do not enable a blanket allow-all HTML rule. Configure an explicit allowlist and continue to enforce the backend HTML sanitizer.
- Enhanced source code editing and export-with-inline-styles are not part of the free baseline assumed for this migration. Keep the existing raw HTML textarea rather than depending on a premium source editor.
- The existing signature group picker inserts into the raw textarea. A CKEditor integration must insert the token at the active editor selection, or deliberately switch to source mode, without losing cursor position.
- The editor must not silently rewrite unsupported email-client-specific markup.

## Proposed migration architecture

1. Build and pin a self-hosted CKEditor 5 GPL bundle. Do not load it from a third-party CDN.
2. Introduce a small editor adapter for the personal, domain and administrator surfaces. It should expose `getData()`, `setData(html)`, `focus()`, `insertTextAtSelection(text)` and `destroy()`.
3. Keep the raw HTML source textarea available. Rich-text changes update the textarea; source edits update the rich-text editor. Prevent feedback loops when synchronizing programmatic updates.
4. Update sanitizer/import, preview, save and clear actions to use the adapter rather than direct Quill APIs.
5. Make the group tag picker insert `{{ group:slug }}` at the active CKEditor selection when rich-text mode is active, and into the source textarea when source mode is active.
6. Initialize the domain and administrator editors lazily and destroy/recreate them safely when their modal lifecycle requires it.
7. Keep backend sanitization authoritative. Editor-side filtering is not a security boundary.

## Required HTML compatibility corpus

Before switching the default editor, test round trips through `setData()` and `getData()` using representative signatures containing:

- Inline styles for font family, font size, color, background, spacing and alignment.
- Tables with nested rows/cells, cell attributes and layout-related styles.
- Links, images, image dimensions and alternative text.
- `div`, `span`, `p`, headings, lists and nested lists.
- Existing template variables such as `{{ first_name }}` and `{{ group:slug }}`.
- Outlook-specific conditional comments and VML samples, which must remain untouched in source mode even if the rich-text view cannot represent them.

For each fixture, compare normalized HTML before and after editing. Any lost attribute, style, element, comment or placeholder is a migration blocker unless explicitly documented and approved.

## Acceptance criteria

- All three editor surfaces work with the same adapter contract.
- Rich-text edits, raw HTML edits, sanitizer/import, preview, save and clear remain synchronized.
- Group tokens insert at the current selection and survive save/reload unchanged.
- The HTML compatibility corpus passes without unexplained data loss.
- Backend sanitizer tests remain green; no broad HTML allow-all policy is introduced.
- The editor bundle is self-hosted, version-pinned and covered by license/dependency checks.
- No commercial CKEditor feature or license key is required.
- The Quill integration is removed only after browser tests cover all three editor surfaces.

## Current status

Assessment complete; migration not started. The immediate next implementation step is a small CKEditor 5 GPL prototype against the compatibility corpus, while keeping Quill as the production editor until the prototype passes.


## Compatibility corpus: first server-side baseline

The first executable compatibility checks are in `tests/test_signature_editor_compatibility.py`. They cover nested presentation tables, common inline email styles, links, images, template tokens and removal of unsafe markup.

**Known blocker discovered:** the current `nh3` sanitizer strips HTML comments and disallowed VML elements. Therefore Outlook conditional comments and VML are not preserved when sanitized content is saved. The test records this current behavior explicitly; it does not treat it as acceptable migration fidelity. Do not enable CKEditor or claim source-mode preservation until a deliberate, security-reviewed policy for Outlook conditional markup exists.

These tests validate the server-side sanitizer baseline only. They are not CKEditor round-trip tests and do not prove rendering in Outlook or other mail clients. Quill remains the production editor.
