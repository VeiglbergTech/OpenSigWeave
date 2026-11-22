# CodeQL security alert triage — 2026-11-22

This report inventories the **28** open MEDIUM/HIGH CodeQL alerts observed on the dev branch at commit 3c7b7bd5b10d8c3078c2d16201c588ed4c57b509. It does not dismiss or suppress alerts or claim they are resolved without a fresh scan.

| Alerts | Severity | Rule | File(s) | Initial assessment / remediation |
| --- | --- | --- | --- | --- |
| 1–18 | HIGH | py/path-injection | identity/registry.py:58,109,156,158,199–218,288–292 | A strict ASCII slug regex accepts only 1–63 alphanumeric/hyphen characters, preventing ../, separators and absolute paths; private .idp is permissioned 0700 and symlink directories and .env files are checked. Added explicit os.path.basename canonicalization. These findings appear to be taint-tracker limitations, **but symlink TOCTOU and misconfigured filesystem ownership need review**, especially if the .idp volume is shared with a hostile process. Only dismiss individually after confirming the deployment threat model. |
| 19 | HIGH | py/overly-permissive-file | tests/test_signature_storage.py:79 | Intentional 0755 **negative test fixture**, immediately verified to be hardened to 0700. Not a production file permission; candidate false positive with documented context. |
| 20–21 | MEDIUM | py/log-injection | main.py:712,1329 | External provider ID was included in logs. Removed uncontrolled provider identifiers from these warning messages. |
| 22–23 | HIGH | js/bad-tag-filter, js/incomplete-multi-character-sanitization | static/js/portal.js:125 | The HTML import attempted to strip scripts using a brittle regex. Replaced it with DOMParser DOM-node removal; server-side signature_sanitizer.py still enforces the nh3 tag/attribute allowlist before storage. |
| 24 | HIGH | py/insecure-protocol | signature_image_import.py:81 | Already used ssl.create_default_context() with hostname verification + original hostname SNI, HTTPS-only URL, public DNS pinning and no redirects. Explicit minimum TLS 1.2 now added, without disabling verification. Re-test with actual CodeQL findings before deciding whether alert was a false positive. |
| 25 | HIGH | py/polynomial-redos | main.py:1444 | Backup filename matching now uses bounded, linear-time ASCII characters/prefix/suffix checks instead of regex. |
| 29–31 | HIGH | py/overly-permissive-file | scripts/backup_control_agent.py:392,394,401 | 0750 directory/0660 Unix socket are explicitly group-accessible so the application can connect; files are root-owned, group APP_GID. Handler enforces SO_PEERCRED UID 0/APP_UID, socket directory traversal is restricted to group and root, no world permissions. Intentional access design; requires verification of host UID/GID assignments and whether an attacker could join APP_GID. Do not dismiss without that review. |

The missing alert numbers 26–28 are historical or non-open; these 28 alerts match the current dev scan and include 26 HIGH, 2 MEDIUM.

## Follow-up

1. Wait for a new full CodeQL analysis of the updated dev commit. Check which alerts persist and whether new ones appear.
2. Validate end-to-end sanitization, provider path abuse and backup socket permissions with realistic threat models, including symlink races.
3. Individually document and (where appropriate) dismiss **only justified false positives** in GitHub Security → Code scanning, referencing this report. Do not disable the CodeQL findings gate for convenience.
4. Confirm 0 open untriaged medium/high/critical alerts before allowing auto-promotion; do not merge beta into main without manual acceptance.
