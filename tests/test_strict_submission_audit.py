"""Read-only strict Mailcow Milter wiring audit tests."""
import unittest

from scripts.audit_mailcow_submission import audit

MAIN = """smtpd_milters = inet:rspamd:9900
milter_default_action = accept
"""
SERVICES = """submission/inet=smtp inet n - y - - smtpd
smtp/inet=smtp inet n - y - - smtpd
"""
OVERRIDES = """submission/inet/smtpd_milters=inet:rspamd:9900,inet:opensigweave-strict-milter:10045
submission/inet/milter_default_action=tempfail
submission/inet/milter_macro_daemon_name=OPENSIGWEAVE_SUBMISSION
"""


class StrictAuditTests(unittest.TestCase):
    def check(self, overrides=OVERRIDES, main=MAIN):
        return audit(main, SERVICES, overrides, "rspamd", "opensigweave-strict-milter")

    def test_correct_order_and_scope(self):
        results = self.check()
        self.assertFalse([m for level, m in results if level == "FAIL"], results)

    def test_missing_independent_milter_rejected(self):
        results = self.check(OVERRIDES.replace(
            ",inet:opensigweave-strict-milter:10045", ""
        ))
        self.assertTrue(any("strict Milter missing" in msg for level, msg in results
                            if level == "FAIL"))

    def test_wrong_milter_order_rejected(self):
        results = self.check(OVERRIDES.replace(
            "inet:rspamd:9900,inet:opensigweave-strict-milter:10045",
            "inet:opensigweave-strict-milter:10045,inet:rspamd:9900"
        ))
        self.assertTrue(any("not after Rspamd" in msg for level, msg in results
                            if level == "FAIL"))

    def test_missing_submission_marker_rejected(self):
        results = self.check(OVERRIDES.replace(
            "OPENSIGWEAVE_SUBMISSION", "ORIGINATING"
        ))
        self.assertTrue(any("missing trusted" in msg for level, msg in results
                            if level == "FAIL"))

    def test_inbound_strict_milter_rejected(self):
        main = MAIN.replace(
            "inet:rspamd:9900",
            "inet:rspamd:9900,inet:opensigweave-strict-milter:10045"
        )
        results = self.check(main=main)
        self.assertTrue(any("Port-25" in msg for level, msg in results
                            if level == "FAIL"))


if __name__ == "__main__":
    unittest.main()
