"""Tests for read-only submission policy audit and controlled SMTP DATA probe."""
import smtplib
import unittest
from unittest.mock import patch

from scripts.audit_mailcow_submission import audit, effective_setting, parse_services, parse_settings
from scripts.mailcow_smtp_probe import submit_message


BASE = "smtpd_milters = inet:rspamd:9900\nmilter_default_action = accept\n"
SERVICES = "smtp/inet=smtp inet n - y - - smtpd\nsubmission/inet=submission inet n - y - - smtpd\nsmtps/inet=smtps inet n - y - - smtpd\n"
STRICT = "submission/inet/milter_default_action = tempfail\nsmtps/inet/milter_default_action = tempfail\n"


# Real "postconf -M" returns whitespace-separated columns, not '='.
# Model the observed Mailcow services, including pass/inet plus the
# dedicated SOGo, HAProxy, quarantine, BCC and watchdog listeners.
MAILCOW_MASTER_OUTPUT = """smtp       inet  n - n - 1 postscreen
10025      inet  n - n - 1 postscreen -o postscreen_upstream_proxy_protocol=haproxy
smtpd      pass  - - n - - smtpd
smtps      inet  n - n - - smtpd -o smtpd_tls_wrappermode=yes
10465      inet  n - n - - smtpd -o smtpd_upstream_proxy_protocol=haproxy
submission inet  n - n - - smtpd -o smtpd_tls_security_level=encrypt
10587      inet  n - n - - smtpd -o smtpd_upstream_proxy_protocol=haproxy
588        inet  n - n - - smtpd -o syslog_name=postfix/sogo
590        inet  n - n - - smtpd -o smtpd_milters=
591        inet  n - n - - smtpd -o smtpd_milters=
589        inet  n - n - - smtpd -o smtpd_milters=
smtp       unix  - - n - - smtp
"""


class PostfixSubmissionAuditTests(unittest.TestCase):
    def test_real_mailcow_postconf_m_output_is_parsed(self):
        services = parse_services(MAILCOW_MASTER_OUTPUT)
        self.assertTrue({"smtps/inet", "submission/inet", "588/inet",
                         "10465/inet", "10587/inet"}.issubset(services))
        self.assertIn("smtp/inet", services)
        self.assertIn("smtpd/pass", services)
        self.assertIn("smtp/unix", services)
        self.assertIn("10465/inet", services)
        self.assertIn("588/inet", services)
        main = "smtpd_milters = inet:rspamd:9900\nmilter_default_action = tempfail\n"
        findings = audit(main, MAILCOW_MASTER_OUTPUT, "")
        self.assertEqual(sum(level == "FAIL" for level, _ in findings), 0)
        self.assertEqual(sum(level == "PASS" for level, _ in findings), 10)
        self.assertTrue(any(level == "WARN" and "Port-25" in msg
                            for level, msg in findings))
        self.assertFalse(any("590:" in msg or "591:" in msg or "589:" in msg
                             for _, msg in findings))

    def test_real_mailcow_service_override_still_fails_closed_audit(self):
        main = "smtpd_milters = inet:rspamd:9900\nmilter_default_action = tempfail\n"
        override = "588/inet/milter_default_action = accept\n"
        findings = audit(main, MAILCOW_MASTER_OUTPUT, override)
        self.assertTrue(any(level == "FAIL" and "588:" in msg for level, msg in findings))

    def test_parse_service_records_ignores_arbitrary_key_value_settings(self):
        services = parse_services("smtpd_milters=inet:rspamd:9900\n"
                                  "submission/inet=submission inet n - n - - smtpd\n")
        self.assertEqual(services, {"submission/inet"})

    def test_per_service_strict_does_not_block_inbound_default(self):
        findings = audit(BASE, SERVICES, STRICT)
        self.assertEqual(sum(level == "FAIL" for level, _ in findings), 0)
        self.assertFalse(any(level == "WARN" for level, _ in findings))
        self.assertEqual(sum(level == "PASS" for level, _ in findings), 4)

    def test_global_fail_open_is_rejected(self):
        failures = [msg for level, msg in audit(BASE, SERVICES, "") if level == "FAIL"]
        self.assertEqual(len(failures), 2)
        self.assertTrue(all("milter_default_action" in msg for msg in failures))

    def test_missing_milter_is_rejected_even_if_action_is_tempfail(self):
        overrides = STRICT + "submission/inet/smtpd_milters = \n"
        failures = [msg for level, msg in audit(BASE, SERVICES, overrides) if level == "FAIL"]
        self.assertTrue(any("smtpd_milters" in msg for msg in failures))

    def test_mailcow_sogo_listener_588_is_a_required_outbound_path(self):
        # SOGo submits to Postfix's internal listener, not public port 587.
        services = SERVICES + "588/inet=588 inet n - n - - smtpd\n"
        findings = audit(BASE, services, STRICT)
        self.assertTrue(any(level == "FAIL" and "588:" in msg for level, msg in findings))
        strict = STRICT + "588/inet/milter_default_action = tempfail\n"
        self.assertFalse(any(level == "FAIL" for level, _ in audit(BASE, services, strict)))

    def test_mailcow_haproxy_submission_services_are_required(self):
        services = (SERVICES
                    + "10465/inet=10465 inet n - n - - smtpd\n"
                    + "10587/inet=10587 inet n - n - - smtpd\n")
        strict = (STRICT
                  + "10465/inet/milter_default_action = tempfail\n"
                  + "10587/inet/milter_default_action = tempfail\n")
        self.assertEqual(
            sum(level == "FAIL" for level, _ in audit(BASE, services, STRICT)), 2
        )
        self.assertFalse(any(level == "FAIL" for level, _ in audit(BASE, services, strict)))

    def test_quarantine_bcc_watchdog_are_not_user_submission(self):
        services = ("590/inet=590 inet n - n - - smtpd\n"
                    "591/inet=591 inet n - n - - smtpd\n"
                    "589/inet=589 inet n - n - - smtpd\n"
                    "588/inet=588 inet n - n - - smtpd\n")
        policy = "588/inet/milter_default_action = tempfail\n"
        findings = audit(BASE, services, policy)
        self.assertFalse(any(level == "FAIL" for level, _ in findings))
        self.assertTrue(any("588:" in msg for _, msg in findings))
        self.assertFalse(any("590:" in msg or "591:" in msg or "589:" in msg
                             for _, msg in findings))

    def test_nonstandard_service_names_are_still_reported_for_manual_review(self):
        findings = audit(BASE, "2525/inet=2525 inet n - n - - smtpd\n", "")
        self.assertTrue(any(level == "FAIL" and "postconf -M" in msg
                            for level, msg in findings))

    def test_inherited_milter_reference_and_unknown_expansion(self):
        main = parse_settings(BASE)
        overrides = parse_settings("submission/inet/smtpd_milters = $smtpd_milters\n")
        self.assertEqual(effective_setting(main, overrides, "submission", "smtpd_milters"), "inet:rspamd:9900")
        overrides = parse_settings("submission/inet/smtpd_milters = $untrusted_variable\n")
        self.assertIsNone(effective_setting(main, overrides, "submission", "smtpd_milters"))

    def test_inbound_global_tempfail_warns(self):
        config = BASE.replace("milter_default_action = accept", "milter_default_action = tempfail")
        findings = audit(config, SERVICES, "")
        self.assertTrue(any(level == "WARN" and "Port-25" in msg for level, msg in findings))

    def test_missing_submission_service_rejected(self):
        findings = audit(BASE, "smtp/inet=smtp inet n - y - - smtpd\n", "")
        self.assertTrue(any(level == "FAIL" for level, _ in findings))

    def test_expected_endpoint_is_verified(self):
        failures = audit(BASE.replace("rspamd", "unrelated-milter"), SERVICES, STRICT)
        self.assertTrue(any("expected Rspamd" in msg for level, msg in failures))

    def test_legacy_smtps_only(self):
        findings = audit(BASE, "smtps/inet=smtps inet n - y - - smtpd\n",
                         "smtps/inet/milter_default_action = tempfail\n")
        self.assertFalse(any(level == "FAIL" for level, _ in findings))


class FakeSMTP:
    def __init__(self, reject_code=None):
        self.reject_code = reject_code
        self.started_tls = False
        self.authenticated = False
        self.sent = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def ehlo(self):
        pass

    def starttls(self, context):
        self.started_tls = True

    def login(self, username, password):
        self.authenticated = True

    def send_message(self, mail, from_addr, to_addrs):
        self.sent += 1
        if self.reject_code is not None:
            raise smtplib.SMTPDataError(self.reject_code, b"controlled test")


class MailcowSMTPProbeTests(unittest.TestCase):
    def check(self, port, reject_code):
        client = FakeSMTP(reject_code)
        def factory(host, selected_port, **kwargs):
            self.assertEqual(selected_port, port)
            self.assertIsNotNone(kwargs.get("context")) if port == 465 else None
            return client
        result = submit_message(
            "smtp.example.test", port, "user", "secret", "user@example.test",
            "test@example.test", ssl_context=object(), factory=factory,
        )
        self.assertEqual(client.sent, 1)
        self.assertTrue(client.authenticated)
        self.assertEqual(client.started_tls, port == 587)
        return result

    def test_451_data_response(self):
        self.assertEqual(self.check(587, 451), ("data_rejected", 451))

    def test_550_is_not_tempfail(self):
        self.assertEqual(self.check(465, 550), ("data_rejected", 550))

    def test_accepted_once(self):
        self.assertEqual(self.check(587, None), ("accepted", None))

    def test_no_plaintext_submission(self):
        with self.assertRaises(ValueError):
            submit_message("smtp.example.test", 25, "user", "secret",
                           "sender@example.test", "test@example.test")


if __name__ == "__main__":
    unittest.main()
