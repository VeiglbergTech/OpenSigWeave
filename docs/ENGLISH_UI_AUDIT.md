# English UI Copy Audit

## Scope

Audited first-party UI copy in `templates/index.html`, `templates/login.html`,
`templates/error.html`, and the UI scripts in `static/js/`. Reviewed
navigation, dialogs, form labels, descriptions, notifications, errors, empty
states, CKEditor-specific actions and the browser smoke-test expectations.

The app uses server-rendered Jinja templates with Alpine.js and first-party
JavaScript strings. There is no existing application-wide translation catalog
to extend, so this change edits the existing copy without introducing an
alternative localization mechanism.

## Changes

- **Navigation:** align labels for Schedules and Backup, with section names
  consistent across navigation, headings and related tests.
- **Login / identity providers:** translate the external-provider sign-in link,
  clarify provider ID, authentication and connection-test labels.
- **Personal signatures / domains / rules / scheduling:** clarify profile and
  override wording, domain-management actions, form controls and schedule titles.
- **Signature editor / CKEditor:** use "Import Image from URL" consistently in
  the toolbar action, dialog and browser smoke tests; standardize raw-HTML labels.
- **Branding:** translate the reset action, confirmation and notifications;
  standardize upload fallback and save-action wording.
- **Backups / import and export:** clarify backup actions, retention options,
  time zone and destination labels; standardize system and import headings.
- **Warnings / system status:** translate both Outlook HTML warnings and align
  refresh, backup status and operational error messages.
- **Tests:** update Playwright/browser smoke assertions to the new English labels
  and add `tests/test_english_ui_copy.py` to catch static-language regressions.

## Boundaries

- No API names, route paths, database schema, stored configuration keys, rules,
  signature content, import payloads or LDAP data are translated.
- Dynamic server/API errors are still rendered as received. They must remain
  meaningful and may contain user-provided content.
- CKEditor's distributed third-party scripts and assets are not modified.
- Operational warnings about backup limitations, import behavior, and safe
  HTML editing are retained rather than removed for brevity.

## Verification

Run:

```sh
python -m unittest discover -s tests -p 'test_english_ui_copy.py' -v
node --check static/js/portal.js
node --check static/js/rich-editor.js
python -m unittest discover -s tests -v
```

The GitHub Actions `tests.yml` workflow on `dev` also runs the browser
smoke test against both light and dark themes, the login and administration
views, modal flows, backup management, and CKEditor. CI outcomes should be
checked on the commit before promotion to `beta` or `main`.
