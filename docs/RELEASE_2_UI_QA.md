# Release 2 — UI acceptance checklist (manual browser verification)

The Backup tab is the reference for card geometry, headings, tables, controls and status chips.
This checklist is a **manual test plan**, not evidence that the checks have been executed.

## Matrix

Run the following for **both light and dark themes** with an administrator and a normal user.
Use desktop browser zoom at **50%, 75%, 100%, 125%, 150%**; also test 320, 375, 768,
1024, 1280 and 1920 CSS-pixel wide viewports at 100% zoom. Do not equate browser
zoom with viewport emulation. Test in Chromium and at least one other browser.

For every viewport/theme combination:

- [ ] Main navigation and all Admin tabs wrap without losing an action or creating
      horizontal document scrolling (`document.documentElement.scrollWidth <= innerWidth + 2`).
- [ ] My Signature: profile panel, CKEditor toolbar, font dropdown, source editor and preview
      remain reachable; at 50% zoom the editor uses the available width.
- [ ] System / Mail Server: configuration toggles, health badge, errors and action buttons fit.
- [ ] Identity Providers: both provider tables and two provider editor dialogs fit; buttons
      remain usable and statuses distinguish active, disabled and incomplete configuration.
- [ ] Domains, Signatures, Signature Groups and Rules: headers align with cells; Action
      controls align with the corresponding heading; rules preserve the intentional Time layout.
- [ ] Branding and Import / Export: controls and warnings remain visible and keyboard accessible.
- [ ] Backup Locations, Rules and History: controls, filters, status chips and detail dialogs fit,
      including 7-column narrow-layout fallback and long destination strings.
- [ ] Modal nesting: open parent and confirmation/image dialog; exactly one dimmed/blurred
      backdrop appears; Escape, Tab trap, focus return and internal scrolling still work.
- [ ] Status chips use consistent dimensions/colors: active/success = green, pending/unverified
      = amber, error/failed = red, inactive/disabled = neutral gray.
- [ ] No clipped floating menus, clipped buttons or invisible interactive controls; links have
      visible non-color affordance, branding action colors apply only to buttons.
- [ ] Screen reader labels and keyboard focus indicators remain usable.

## Existing automated coverage

- `python -m unittest tests.test_global_design_system tests.test_ui_global_consistency tests.test_table_layout tests.test_signature_editor_responsive`
- The existing `tests/browser_smoke.cjs` contains browser/UI checks, but requires the local
  test server, Playwright and configured test users and must be run independently.

Do **not** mark the Release 2 UI acceptance complete until the browser matrix has actually
been executed and any defects have been fixed.
