"""Contract tests for the optional last-in-chain strict Milter."""
import importlib
import sys
import types
import unittest
from unittest import mock


class FakeMilterBase:
    def __init__(self):
        self.macros = {}
        self.reply = None

    def getsymval(self, key):
        return self.macros.get(key)

    def setreply(self, code, enhanced, text):
        self.reply = (code, enhanced, text)


fake_milter = types.SimpleNamespace(
    Base=FakeMilterBase, CONTINUE=0, TEMPFAIL=1,
    factory=None, set_flags=lambda flags: None,
    runmilter=lambda *args: None,
)

with mock.patch.dict(sys.modules, {"Milter": fake_milter}):
    mod = importlib.import_module("scripts.opensigweave_strict_milter")


class StrictMilterTests(unittest.TestCase):
    def _instance(self, auth="u@example.test", queue="ABCDEF0123", submission=True):
        instance = mod.StrictReceiptMilter()
        instance.macros = {
            "{auth_authen}": auth,
            "i": queue,
            "{daemon_name}": "OPENSIGWEAVE_SUBMISSION" if submission else "smtp",
        }
        self.assertEqual(instance.envfrom("<u@example.test>"), fake_milter.CONTINUE)
        return instance

    def test_after_rspamd_healthy_receipt_allows_message(self):
        instance = self._instance()
        with mock.patch.object(mod, "verify_receipt", return_value=True) as verify:
            self.assertEqual(instance.eom(), fake_milter.CONTINUE)
            verify.assert_called_once()
            args = verify.call_args.args
            self.assertEqual(args[:3], ("ABCDEF0123", "u@example.test", "u@example.test"))

    def test_both_plugins_missing_no_receipt_is_tempfail(self):
        instance = self._instance()
        with mock.patch.object(mod, "verify_receipt", return_value=False):
            self.assertEqual(instance.eom(), fake_milter.TEMPFAIL)
        self.assertEqual(instance.reply[:2], ("451", "4.7.1"))
        self.assertIn("OpenSigWeave", instance.reply[2])

    def test_untrusted_inbound_is_unchanged(self):
        instance = self._instance(auth="")
        with mock.patch.object(mod, "verify_receipt") as verify:
            self.assertEqual(instance.eom(), fake_milter.CONTINUE)
            verify.assert_not_called()

    def test_no_auth_on_submission_tempfails_instead_of_bypass(self):
        instance = self._instance(auth="", submission=True)
        self.assertEqual(instance.eom(), fake_milter.TEMPFAIL)
        self.assertEqual(instance.reply[0], "451")

    def test_missing_queue_id_tempfails(self):
        instance = self._instance(queue="")
        with mock.patch.object(mod, "verify_receipt", return_value=False):
            self.assertEqual(instance.eom(), fake_milter.TEMPFAIL)

    def test_unreachable_receipt_service_does_not_allow(self):
        with mock.patch.object(mod.socket, "create_connection", side_effect=OSError):
            self.assertFalse(mod.verify_receipt(
                "ABCDEF0123", "u@example.test", "u@example.test",
                host="127.0.0.1", port=10043,
            ))

    def test_crlf_and_missing_identity_never_reach_service(self):
        with mock.patch.object(mod.socket, "create_connection") as conn:
            self.assertFalse(mod.verify_receipt(
                "ABCDEF0123\\nspoof", "u@example.test", "u@example.test",
                host="127.0.0.1", port=10043,
            ))
            self.assertFalse(mod.verify_receipt(
                "", "u@example.test", "u@example.test",
                host="127.0.0.1", port=10043,
            ))
            conn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
