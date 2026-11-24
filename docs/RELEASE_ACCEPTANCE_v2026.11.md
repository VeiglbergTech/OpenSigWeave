# 2026.11-alpha early-access acceptance record

**Status:** NOT APPROVED FOR `main`, version tag or GHCR publication. This is an **alpha pre-release**, not 1.0 Stable. Record the tested features and known omissions from the [mail usage acceptance matrix](MAIL_USAGE_ACCEPTANCE_MATRIX.md); explicit maintainer sign-off on remaining alpha limitations is required. No checkbox is auto-approved by CI.

## Automated gates — require final candidate SHA

- [ ] Record exact `dev` SHA and all required **push** workflow run links for that same SHA.
- [ ] Regular **Tests** workflow: success, all jobs. Include the full Python regression suite, migrations, browser/CKEditor test, Docker Compose build/startup/health and real encrypted backup/fresh-target restore.
- [ ] **Security scan** workflow: success, all CodeQL (Python/JavaScript), Trivy repository and Docker image scans, `pip-audit`, Bandit and historical Gitleaks. Review **unfixed** HIGH/CRITICAL findings from the image-scan report separately.
- [ ] Postfix Milter Compatibility CI succeeds for the candidate if triggered; it is not evidence of real Mailcow/Rspamd success.
- [ ] Confirm no secrets, real accounts, private hostnames, `.env`, private keys, archives, logs or debug artifacts were committed.
- [ ] Record the exact tested `beta` SHA. Use only a `dev -> beta` promotion after fully green source CI and required PR checks.
- [ ] Verify main remains untouched until explicit approval. After approval, create `YYYY.MM-alpha[N]` from the exact approved main SHA and verify the [self-hosted GHCR workflow](RELEASE_PROCESS.md) published only the version tag and `alpha` alias.

## Environment acceptance (record alpha limitations)

- [ ] Deploy the exact candidate to an isolated host with HTTPS/reverse proxy and explicit allowlist; verify `docker compose ps`, migration state and System diagnostics.
- [ ] Test emergency local administrator, Authentik/generic OIDC as used, and LDAPS login/logout, verified-email and group mapping; verify least privilege and negative tests.
- [ ] Test domains, personal overrides, disabled overrides, active signatures, version creation, historical preview and restore-as-new without history loss.
- [ ] Test signature groups, weights, no-repeat selection, nested group placeholders, priority rule matching and `NONE` rule.
- [ ] Test timezone/DST boundary, overlapping domain/global schedule precedence, before/during/after behavior, restart persistence and Rspamd engine resolution.
- [ ] Upload/paste/drag PNG/JPEG/WebP in CKEditor; verify reload, historical asset reuse, anonymous external image GET, unauthorized POST rejection and 2 MiB limit.
- [ ] Change and reset branding name/color/logo/favicon, reload/restart, and verify persistence, secure validation and cache behavior.
- [ ] Test browser import/export only as selective configuration transfer; ensure secrets, identities and overrides are excluded.
- [ ] Perform a **two-host** encrypted backup verification and fresh-target restore; test a real mounted NFS/SMB disconnect/recovery if used in production.
- [ ] Verify HTTP security headers and browser behavior (CSP remains Report-Only) with actual supported clients.
- [ ] Test sustained realistic load and adapter downtime/recovery; record CPU/memory and whether failure is fail-open/fail-closed.

## Mailcow/Rspamd SMTP gate — alpha evidence is not production certification

Use the full [Mailcow acceptance matrix](MAILCOW_RSPAMD_INTEGRATION.md#manual-acceptance-checklist-mailcow-beta). Record raw `.eml` evidence, client/version and SHA-256 of every test attachment.

- [ ] Authenticated envelope sender matches the mailbox or explicitly delegated alias. Rejected sender spoofing has been tested.
- [ ] New plain, HTML, multipart/alternative, nested related/mixed, long headers, Unicode, quoted-printable/base64 with SOGo, Outlook and Thunderbird.
- [ ] PDF/JPEG/ZIP attachment hashes and inline CID images remain intact and visible.
- [ ] Replies/forwards place the new signature before prior quoted history; resubmission and changed/rotated signatures are checked for duplicate limitations.
- [ ] Signed/encrypted and attached `message/rfc822` messages are skipped without mutation.
- [ ] Engine outage, wrong token, TLS failure, size/part limits, restarts and SMTP queue health are observed and fail-open consequences accepted explicitly.
- [ ] Final external recipient DKIM signature validates **after** all Mailcow content changes; filter order and competing footers verified.

## Approval / evidence

Record: exact `dev` SHA, exact `beta` SHA, CI links, test host, Mailcow/Rspamd and mail-client versions, test date, administrator, unresolved findings and mitigation. Obtain **explicit manual** approval before `beta -> main`. Do not infer permission from a checked box, green CI, merged PR or this document.
