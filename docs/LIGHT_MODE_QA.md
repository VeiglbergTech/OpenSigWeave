# Light Mode: design and validation

This page describes the theme unification on the `dev` branch (2026-11-23).

## Architecture

- `static/css/portal.css` retains the existing dark theme as the default. Its single `:root[data-theme="light"]` overlay replaces **surface and foreground tokens**, rather than inverting the dark palette.
- The structure remains: `--osw-black` (application canvas), `--osw-charcoal` (table and dialog headers), `--osw-panel` (cards, navigation, dialogs), `--osw-panel-raised` (secondary controls), `--osw-input` (form inputs), `--osw-border`, `--osw-text` and `--osw-muted`.
- `static/js/theme.js` continues to own selection, persistence, mode-aware logo switching, and the `dark` class. `static/js/branding.js` continues to compute readable branding accents for the selected theme; no parallel color system is introduced.
- Portal layout, native form controls, status and warning panels, action buttons, tab and table states, dialogs and CKEditor chrome follow those tokens. **CKEditor email content remains white** in both themes, because emails need a stable preview.
- Locked-editor scrims are light in Light Mode; the actual backdrop behind modal dialogs stays dark to keep dialogs distinguishable.

## Light palette and contrast

| Surface / role | Background | Foreground | Contrast |
| --- | --- | --- | --- |
| Canvas / text | `#f2f5f9` | `#1e293b` | 13.38:1 |
| Card / text | `#ffffff` | `#1e293b` | 14.63:1 |
| Card / muted text | `#ffffff` | `#526174` | 6.32:1 |
| Primary action (unbranded token fallback) | `#a63b1b` | `#ffffff` | 6.44:1 |
| Error action | `#b91c1c` | `#ffffff` | 6.47:1 |
| Warning | `#fffbeb` | `#854d0e` | 6.61:1 |

Ratios above are calculated from the default static hex values using the WCAG relative-luminance formula. With the shipped default branding color `#ff7043`, `branding.js` selects a darker effective button color (`#c25533`, approximately 4.52:1 against white). User-configured branding colors are adjusted at runtime by `branding.js` and must be checked separately.

## Test coverage

The existing Playwright suite (`tests/browser_smoke.cjs`) now checks both themes on sign-in, the personal editor, and **every administrator tab**: System, Backups, Identity Providers, Domains, Signatures, Signature Groups, Rules, Scheduling, Branding, Exports, and Imports. It tests text/input/button contrast, card/navigation separation, Light Mode table headers, persisted selection, CKEditor menu contrast, and representative admin and signature dialogs.

The same suite captures screenshots for each theme and tab, the login and personal editor views, the provider dialog, and the signature editor. They are uploaded by the **Tests** GitHub Actions workflow as `dialog-visual-regression` from `test-artifacts/themes/` and `test-artifacts/dialogs/`.

**Important:** these screenshot artifacts are review aids, not pixel-diff/golden-image comparisons. The computed-style and contrast assertions are automated; actual screenshot comparison and review across mobile viewport sizes still require human evaluation.

## Manual review checklist

1. At 1440px, 768px, and 390px widths, open each administrator tab in Light and Dark Mode.
2. Check empty and populated tables, menu hover, native selects, status states, dialogs, disabled form fields, scrolling and narrow screens.
3. Test CKEditor dropdowns, color swatches, signature white canvas, modal editor and font toolbar.
4. Apply a bright and a dark custom branding accent, switch themes, then reset branding. Verify text contrast, action colors and logo variants.
5. Check focus-visible state with keyboard navigation and inspect `dialog-visual-regression` artifacts for clipping or misalignment.

Passing CI confirms the automated checks, not a complete visual or accessibility audit. Review the run associated with the latest `dev` commit before promoting to `main`.
