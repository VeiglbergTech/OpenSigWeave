# Rich-text editor decision

**Decision:** Keep the vendored Quill 1.3.7 editor. Do not replace it until a self-hosted CKEditor 5 GPL proof of concept passes the email-HTML compatibility tests.

The editor must:
- Work without a third-party CDN at runtime.
- Preserve email-compatible HTML and existing signature source.
- Keep backend sanitization authoritative.
- Avoid commercial-only features, metering and incompatible licensing.
- Retain the raw HTML source editor for markup the visual editor cannot represent.

CKEditor 5's self-hosted GPL 2+ distribution is a candidate for this GPL-3.0-or-later project. Do not use its cloud CDN build with the GPL key. Quill remains the current editor; the two observed `style-src-attr` CSP reports are not grounds for adding broad `unsafe-inline` permissions.

**Next step:** Build a separate CKEditor prototype and validate all three editor surfaces, HTML round trips, sanitizer behavior, Outlook-specific markup, group-token insertion, offline operation and licensing before any migration.

See [EDITOR_MIGRATION_PLAN.md](EDITOR_MIGRATION_PLAN.md) for the integration design, compatibility corpus, acceptance criteria and known sanitizer blocker.
