# OpenSigWeave — mail usage acceptance matrix

**Scope:** actual email processing and resulting messages, **not** Web UI operation, installation workflow, backup, branding or administration ergonomics.

**Baseline:** `dev` commit `29505cc4` (24 November 2026); [Tests](https://github.com/VeiglbergTech/OpenSigWeave/actions/runs/45479461644) and [Security scan](https://github.com/VeiglbergTech/OpenSigWeave/actions/runs/45479461527) succeeded. Re-record the exact SHA when testing a newer build. Never interpret green unit/fixture tests as real SMTP, DKIM or recipient-client certification.

**Environment observations (24 November 2026, operator-reported; raw .eml and cryptographic verification outputs not archived):**
- Mailcow / Rspamd with SOGo 5.12.13: CID images in a newly delivered signature **work**.
- New signature in a SOGo reply appears before quoted history **as intended**.
- With `failure_policy = "tempfail"`, SOGo received SMTP `4.7.1` on a real `CID_MIME_REWRITE_FAILED` attempt, so this *specific* technical failure was not silently accepted.
- Earlier CID errors were fixed; the final `rspamd_text` handling fix is `29505cc4`.
- **Controlled 24 November 2026 live outage tests (operator-reported, Postfix + SOGo logs provided):** Rspamd stopped at 22:18:48; Postfix's `postfix/sogo/smtpd` recorded `host or service rspamd:9900 not found`, then `NOQUEUE: milter-reject: CONNECT ... 451 4.7.1 Service unavailable` and a second `NOQUEUE: milter-reject: EHLO ... 451 4.7.1`, with `auth=0/1`. This **passes E04 for the SOGo / port-588 path only**; SOGo misleadingly displayed `(smtp) authentication failure`. Following Rspamd restoration, a new message with signature was sent successfully. Separately, when the OpenSigWeave backend was stopped (Rspamd online) at 22:17:24, Postfix recorded `milter-reject: END-OF-MESSAGE` with `4.7.1`, failure ID `e25097f2`, and `data=0/1`: the backend-outage failure was not queued. These observations do not prove other submission listener paths, missing-plugin detection, or PostgreSQL outage handling.
- **Missing-plugin guard LIVE PASS (24 November 2026, 22:39:01, SOGo/588):** prior to enabling the separate guard, Rspamd running without `opensigweave.lua` accepted an unsigned message (Postfix LMTP `status=sent`, queue ID `859C880088`, 22:22:19). After enabling `opensigweave_guard.lua`, disabling only the injector caused `SIGNATURE_PLUGIN_NOT_LOADED` in Rspamd and `milter-reject: END-OF-MESSAGE ... 4.7.1` in Postfix, queue ID `EA05980088`, failure ID `1dababed`. SOGo displayed the expected temporary rejection. This verifies the opt-in guard **only when the guard itself remains active**; removing both plugins or a silently nonfunctional injector can still bypass the safeguard. An independent Postfix-side sign-required gate remains open.
- **Local DKIM verification observed (operator-reported):** a saved `test.eml` returned `DKIM: PASS` with `dkimpy` after a Mailcow / OpenSigWeave test. This proves the verified DKIM signature matches the saved message and the DKIM public key at verification time; it does not independently establish whether the saved EML contains every desired signature/CID part, or whether an external recipient independently accepted it. Full external-recipient E08 remains open.
- **Additional reported tests (24 November 2026):** rotation, negative-policy cases and integrity checks worked in the isolated environment. Under a reported PostgreSQL outage, outgoing SMTP returned OpenSigWeave `4.7.1` (failure ID `11bba0f2`) and Web UI edits were unavailable. After PostgreSQL was restarted, the operator confirmed the Web UI was fully available and editable again **and signature delivery succeeded without problems**. This supports recovery of both administration and SMTP signing in the isolated test. Exact server-side outage correlation is not archived. LDAP, simultaneous injector+guard removal, and external Outlook acceptance are still unverified.
- **Further functional live checks (24 November 2026, operator-reported, SOGo/Mailcow):** general rules, timed rules, CC/BCC, replies with nested quoted conversations (SOGo only), MIME/CID rendering, attachments, per-domain fallback behavior and Authentik-backed identity all worked in manual tests. These are **observed functional passes**, not exhaustive per-edge-case certification. LDAP integration was **not tested**. Outlook 2016 remained inconclusive after SMTP/TLS/transfer issues in the disposable self-signed-certificate environment; the operator chose to defer it.
- Final external-recipient DKIM validation, attachment integrity and outage handling through other SMTP submission paths remain unverified. The Mailcow test logs also contain an unrelated `Unable to look up MX host` warning for the test recipient's domain; validate DNS separately before interpreting external-delivery tests.

## Failure-policy implementation update (26 November 2026)

The currently supported modes are `disabled` and `enabled`. The legacy
`standard`/`tempfail` values are accepted aliases for `enabled`. The
independent strict receipt service and second Postfix Milter were removed
from the development branch in favor of a separate host-side module health
check. Historical experimental evidence below is retained for traceability.

The new read-only checker verifies the presence/readability of four Rspamd
Lua files and whether the injector and guard are listed as enabled modules.
Its status is **not proof** of an active signature injection, and it cannot
block mail when both modules are missing. An SMTP end-to-end acceptance test
remains necessary, especially after updating Mailcow or Rspamd.

**Newly supplied operator evidence (26 November 2026):** After intentionally
disabling both OpenSigWeave Lua modules while Rspamd remained healthy, two
authenticated test messages were accepted by Postfix Submission and saved via
Dovecot LMTP. Queue IDs `668ED80108` and `9DA6580108` both have
`dsn=2.0.0 status=sent` and were removed from the queue. This is a
reproducible **old-mode bypass** demonstrating why the second independent
Milter is required. The operator subsequently restored both Lua modules.
The operator additionally reported successful live tests on SMTP ports
587 and 465, external DKIM verification and attachment integrity; exact
redacted output for those assertions is not archived in this file. Thunderbird
was observed to show the temporary SMTP error with a correlation ID.

Current acceptance requires `check_mailcow_modules.py` to return
exit 0 with `--policy enabled`, plus an authenticated SMTP end-to-end
test with exactly one signature, appropriate temporary rejection on a
technical engine failure, and recovery. No guarantee exists if both Lua
modules disappear while Rspamd remains active.


## Status notation and evidence

- **CI:** automated Python/Lua/MIME test exists and passed for the baseline (fixture/code behavior, **not** live SMTP).
- **Observed:** operator confirmed live behavior in the stated environment, but the raw message and reproducible artifacts have not been archived here.
- **LIVE:** still needs a controlled test with saved redacted `.eml`, exact client/version, relevant hashes and expected result.
- **Safety:** use only an isolated test domain/test mailbox. Do not intentionally stop Rspamd, alter the Milter or break authentication for production senders. Avoid real personal data in fixtures.

## A. Sender, identity and domain policy

| ID | Case / input | Required outcome | Evidence |
|---|---|---|---|
| A01 | SMTP AUTH with matching authorized envelope sender | One correctly selected signature; no sender spoofing | LIVE; engine tests CI |
| A02 | AUTH user submits as unauthorized different mailbox | Postfix denies impersonation *before* unsigned delivery | LIVE; MTA-owned |
| A03 | Explicitly permitted alias / delegated send-as | Signature of the actual permitted envelope sender; no foreign identity bleed | LIVE; MTA-owned |
| A04 | Incoming unauthenticated mail | No outbound signature injection or OpenSigWeave tempfail | CI: `rspamd_injection_test.lua`; LIVE |
| A05 | Unconfigured sender domain | No signature; intentional skip is not a technical SMTP failure | CI: engine; LIVE |
| A06 | Configured but inactive sender domain | No signature and no unintended rotation | CI: engine; LIVE |
| A07 | Known identity with complete LDAP/Authentik attributes | Live values take priority over domain fallbacks | **Observed:** Authentik-backed user identity and general fallback processing function; LDAP variant and detailed precedence still LIVE |
| A08 | Known identity with incomplete/blank attributes | Fill each missing supported field from *own* domain, never email address | **Observed:** domain fallback works in manual test; per-field precedence/address protection not individually evidenced |
| A09 | Unknown user in configured domain with fallbacks | Signature uses safe per-domain fallbacks; unknown user is not provider outage | CI: `test_domain_fallbacks.py`; LIVE |
| A10 | Unknown user, no fallbacks | Missing fields stay blank; no fake name from email local part | CI: `test_domain_fallbacks.py`; LIVE |
| A11 | One user/address across two configured domains | Correct domain rules, fallbacks and template scope; no cross-domain leak | CI: `test_domain_fallbacks.py`; LIVE |
| A12 | Identity provider unavailable; fail-open then strict identity lookup | Cache/fallback permitted only in documented fail-open mode; strict mode returns 503 → Milter tempfail | CI: `test_domain_fallbacks.py`; LIVE |
| A13 | Generic OIDC cached claims expire | Respect documented offline cache semantics; do not claim real-time OIDC outage detection | CI: identity tests; LIVE |

## B. Policy selection and rotation

| ID | Case / input | Required outcome | Evidence |
|---|---|---|---|
| B01 | No matching rule / deliberate NONE action | No signature; deliberate skip must **not** return SMTP 4xx | **Observed:** negative-policy cases passed per operator; individual cases not separately archived |
| B02 | Default rule versus more specific rule | Priority, multiple AND conditions, negative conditions honored | **Observed:** rule functionality works; exhaustive priority/AND/negative cases still LIVE |
| B03 | Disabled signature, group, rule or domain scope mismatch | Never select disabled or cross-scoped material | **Observed:** negative cases passed per operator; full disabled/scope combination matrix not archived |
| B04 | Personal override enabled / admin-disabled | Override precedence and disabled-user exclusion respected | CI: `test_engine_service.py`, `test_signature_api.py`; LIVE |
| B05 | Valid named selector + four-character checksum | Exact permitted domain/global signature, marker removed | CI: `rspamd_marker_selector_test.lua`; LIVE |
| B06 | Multiple selector markers (identical or different) | Last marker in the newly authored portion selects the signature and is replaced; earlier markers remain unchanged; HTML is authoritative when MIME alternatives disagree; an invalid final checksum must not fall back to an earlier marker | CI: Lua selector and replacement fixtures; LIVE still required |
| B07 | Timed rule versus always-on rule / personal override | Active timed rule wins; before/after window normal rules resume | **Observed:** timed rules function; exact before/after and override precedence still need targeted proof |
| B08 | Schedule at start/end, timezone/DST, overlapping domains | Exact half-open window and scoped priority; stable boundaries | CI: `test_scheduling.py`; LIVE with controlled clock |
| B09 | Group of at least two weighted signatures, sequential mail | Random/weighted selection; never the same as immediately previous selection for that principal/group | **Observed:** signature rotation passed per operator; exact weighted/no-repeat sequence not archived |
| B10 | Group containing exactly one active signature | Repeating the sole candidate is allowed | **Observed:** signature rotation passed generally; single-signature case not individually evidenced |
| B11 | Parallel submissions by same and different principals | Per-principal no-repeat state; no unrelated sender lock contention | **Observed:** rotation works; concurrent multi-sender load test not evidenced |
| B12 | Nested group placeholders and repeated placeholder | One consistent resolved member per email; escaped variables and no unintended extra selection | CI: renderer/API; LIVE |
| B13 | Retry after a failed SMTP transaction | Check *delivered* signatures and rotation history explicitly; engine HTTP lookup may advance selection before Milter SMTP acceptance | **KNOWN TRANSACTIONAL LIMITATION — LIVE** |

## C. Message format, placement and integrity

| ID | Case / input | Required outcome | Evidence |
|---|---|---|---|
| C01 | Fresh plain-text-only message | Readable plain alternative and correctly placed signature, no stray CID parts | CI: `rspamd_injection_test.lua`; LIVE |
| C02 | Fresh HTML and multipart/alternative | Correct HTML + plain representations, not duplicated in visible output | CI: Lua + Python MIME parser; LIVE |
| C03 | SOGo 5.12.13 reply with earlier signatures | New signature after new text but before attribution/quoted history, older signatures untouched | **Observed functional pass:** SOGo replies, including nested quoted history, worked; saved raw EML evidence not archived |
| C04 | Forward from SOGo with nested quoted mail | New signature before forwarded history; no corruption | **Partial observed:** nested quoted conversations worked in SOGo; explicit forward scenario not separately confirmed |
| C05 | Outlook (classic/new) reply and forward | Correct chronology in both MIME alternatives for each tested version | CI: synthetic patterns; **LIVE** |
| C06 | Thunderbird reply and forward | Correct chronology, including cite-prefix/blockquote markup | CI: synthetic patterns; **LIVE** |
| C07 | Quoted-printable and base64 parts with UTF-8 | Valid decoding, umlauts/emoji intact, valid outgoing Content-Transfer-Encoding | CI: Lua/Python MIME fixtures; LIVE |
| C08 | Long/folded headers and unusual charset | Headers preserved; unsupported charset must not silently corrupt message | CI: MIME matrix; LIVE |
| C09 | Nested mixed → alternative → related MIME | All MIME boundaries remain parseable, all text leaves coherent | CI: `rspamd_mime_matrix_test.lua`; LIVE |
| C10 | Attach PDF, JPG and ZIP | Attachment filenames/dispositions and *decoded SHA-256 bytes* unchanged | **Observed:** attachments and integrity checks passed per operator; per-file SHA-256 outputs not archived |
| C11 | Already existing inline image/CID in original email | Original CID and bytes preserved, new CID distinct | CI: `rspamd_cid_test.lua`; LIVE |
| C12 | Sign twice / queue reprocessing / resent message | No duplicate new signature; preserve prior legitimate quoted signature | CI: Lua idempotency; LIVE |
| C13 | S/MIME / PGP signed or encrypted, message/rfc822 attachment | Atomic protected-message skip; no invalidated signature or rewritten attachment | **Reported:** general integrity checks passed; specific S/MIME/PGP protected-message behavior not evidenced |
| C14 | CC, BCC and multiple recipients | Same authorized sender-based signature; no BCC disclosure or recipient-dependent mutation | **Observed functional pass:** CC and BCC worked; raw-header BCC privacy evidence not archived |

## D. CID and rendering

| ID | Case / input | Required outcome | Evidence |
|---|---|---|---|
| D01 | One managed signature image | `multipart/related`, `src="cid:..."`, matching unique Content-ID, inline payload decodes; visible at recipient | **Observed functional pass:** MIME/CID and rendered signature images work in SOGo; raw structural evidence not archived |
| D02 | Two images, repeated reference to one image | One MIME part per unique asset, all HTML references resolve | CI: `test_cid_assets.py`; LIVE |
| D03 | Existing CID + new signature CID | No collisions, no missing original CID or attachment | CI: CID wrapper; LIVE |
| D04 | Hosted image imported once, source URL later unreachable | Delivery uses managed bytes, not original source or public OpenSigWeave asset URL | CI: `test_cid_assets.py`; LIVE with blocked external image loading |
| D05 | Unknown external image URL in signature | Never fetched on SMTP path; CID API rejects unsupported external image rather than leaking it | CI: `test_cid_assets.py`; LIVE |
| D06 | Missing/corrupt/unavailable image asset | Stable engine error; required signature triggers 4xx in enabled mode | CI: engine/CID; LIVE |
| D07 | Max image count, size and response limits | Safe explicit rejection / no partial malformed message | CI: CID/Rspamd; LIVE controlled limits |
| D08 | Recipient blocks remote content / mobile client | CID image renders without externally loading the signature server; record client/version | **LIVE** |

## E. Failure and mail transport behavior

| ID | Case / input | Required outcome | Evidence |
|---|---|---|---|
| E01 | Engine HTTP 401, 422, 503, malformed JSON | No unsigned 250 acceptance in tempfail mode; traceable correlation ID | CI: Lua fail-closed; **Observed 4xx for MIME fault only**; LIVE variants |
| E02 | DNS, TLS certificate, timeout or network failure to engine | Bounded request; SMTP 4xx in tempfail mode | CI: Lua mocks; LIVE |
| E03 | Malformed/unsupported MIME or failed `set_message()` | No damaged email delivered; enabled transient errors deferred, protected intentional skips pass | CI: Lua mocks; LIVE |
| E04 | Stop Rspamd entirely on disposable submission server | All configured authenticated submission paths (including Mailcow SOGo port 588 and HAProxy 10465/10587) must return 4xx; no bypass via `milter_default_action=accept` | **LIVE PASS for SOGo/588 at 22:18:48:** Postfix CONNECT and EHLO returned 451 4.7.1 NOQUEUE; SOGo UI misreported SMTP AUTH error. Public 587/465 and HAProxy paths remain LIVE |
| E05 | Postfix → Rspamd Milter unavailable, Rspamd Lua fails to load | Same 4xx guarantee on authenticated port 587/465, not untrusted port-25 changes | **LIVE PASS (guard enabled, SOGo/588):** 22:39:01 injector missing → `SIGNATURE_PLUGIN_NOT_LOADED`; Postfix EOM `milter-reject 4.7.1`, failure ID `1dababed`. Without guard: unsigned message delivered at 22:22:19. **Not an independent Postfix guarantee**; other listeners and guard-absent scenario still LIVE |
| E06 | OpenSigWeave app or PostgreSQL unavailable | SMTP temporary rejection for signature-required sender; recovery permits single signed send | **LIVE PASS for stopped engine on SOGo/588:** EOM 4.7.1 / `e25097f2`, recovery successful. **Observed under PostgreSQL outage:** SMTP 4.7.1 / `11bba0f2`, Web UI edits unavailable; after DB restart, Web UI fully available **and signature sending successful** (operator-confirmed). Server-side outage correlation not archived |
| E07 | Deliberate empty signature / reply injection disabled | No 4xx just because signature is intentionally absent | CI: Lua; LIVE |
| E08 | Final external delivered DKIM verification | DKIM passes **after** message rewriting; all recipient MIME parts valid | **PARTIAL LIVE PASS:** operator checked saved `test.eml` using `dkimpy`: `DKIM: PASS` (local verification). Confirm EML contains inserted OpenSigWeave signature/CID; external recipient validation and all MIME parts remain LIVE |
| E09 | Mailcow's own footer or other Milter enabled | No duplicate/conflicting signatures or post-DKIM modification | LIVE |
| E10 | Repeated 4xx and successful retry | No message loss, duplicate sends, unexpected queue acceptance or retry loop | LIVE |
| E11 | Attachment/body near 20 MiB, >256 MIME parts, large headers | Safe bounded skip/failure with no corruption; observe CPU/memory and timeout | CI: MIME guards; LIVE |
| E12 | Independent optional support notifier, relay outage/recovery | Deduplicate and persist alerts without signing/recursion or leaking mail content | CI: `test_support_alert_notifier.py`; LIVE if enabled |

## Running the remaining live acceptance

1. **Non-destructive, isolated mailbox:** Start with A01/A05/A07-A11, B01/B02/B04/B05/B09, C01/C02/C04/C07/C10/C11/C12/C14 and D02/D03/D04. Record sender, envelope sender, version, expected rule/signature and redacted received raw EML. Compare attachment SHA-256 and exact Content-ID references. Do not infer successful DKIM from a `DKIM-Signature` header alone.
2. **Compatibility:** Test C05/C06/D08 with the exact Outlook and Thunderbird builds and an external recipient that reports `dkim=pass`; repeat if client quoting settings differ.
3. **Failure lab only:** Perform E01-E07 and E10-E12 on deliberately isolated Mailcow submission infrastructure. Before changing anything, run `scripts/audit_mailcow_submission.py` read-only, then use `scripts/mailcow_smtp_probe.py --expect tempfail` and `--expect accepted` as documented in [Mailcow integration](MAILCOW_RSPAMD_INTEGRATION.md). Stop Rspamd **only if** this is a disposable lab and inbound SMTP is unaffected.
4. **Performance:** Check B11, retries, mail queue, request latency, rate limits and max-size boundaries with harmless synthetic messages. Measure without putting regular accounts at risk.

**Acceptance rule:** An item with only CI evidence is **not** live-pass. A production Mailcow release remains **unapproved** while E04/E05 across other applicable submission paths, monitoring when both injector and guard are missing, E08 (DKIM), relevant real-client tests and attachment hash integrity are unverified. `dev` is the only development branch; `beta` promotion remains CI-gated; `main` requires explicit approval.
