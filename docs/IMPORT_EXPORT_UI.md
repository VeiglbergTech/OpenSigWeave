# Import / Export administration

The administrator portal presents **Import / Export** as a single tab after **Backups**.
Import appears first, followed by Export. Existing Alpine managers remain independent within
this view, with their original form controls and API calls.

## Navigation and compatibility

- The canonical URL is `/?view=admin&section=import-export`.
- Old `?section=imports` and `?section=exports` URLs (including browser history)
  resolve to the combined tab and are replaced with the canonical URL.
- Saved administrator tab state from older releases is normalized.
- Other administration tabs retain their original relative order.

## Retained API contracts

- `GET /api/admin/exports?components=...` generates the same ZIP archive.
- `POST /api/admin/imports/preview` validates an archive before changes.
- `POST /api/admin/imports/apply` applies only a reviewed, approved import.
- Export component selection, archive-size limit, conflict checks, confirmation
  prompt, authorization rules and existing file formats are unchanged.
- This UI consolidation does not change the backend or database schema.

## Verification

The browser smoke test now checks:
1. Exact navigation order and single combined tab in light and dark mode.
2. Legacy imports/exports URLs and visible Import and Export headings.
3. ZIP export download and reuse of that archive via import preview.
4. Validation errors for invalid ZIP data and truncated ZIP headers.
5. Clearing file selection disables validation and resets the preview.
6. Responsive layout checks on desktop and mobile.

The existing `tests/test_admin_export.py` and `tests/test_admin_import.py`
continue to cover the archive format and import processing.

Run `node --check static/js/portal.js`, `node --check tests/browser_smoke.cjs`,
and the repository CI test/browser workflows. Browser and backend tests are
not claimed as passed until those checks have executed.
