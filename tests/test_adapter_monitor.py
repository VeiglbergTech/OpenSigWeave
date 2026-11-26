"""Adapter health and setup contract tests."""
import json
import unittest
from core.adapter_monitor import probe, validate_controller_url, adapter_instructions


class AdapterHealthTests(unittest.TestCase):
    def test_private_controller_allowed(self):
        self.assertEqual(validate_controller_url("http://10.35.12.29:11334"),
                         "http://10.35.12.29:11334")

    def test_public_and_injected_urls_denied(self):
        for bad in ("http://8.8.8.8:11334", "http://127.0.0.1:11334/symbols",
                    "http://user:pass@10.0.0.1", "file:///etc/passwd",
                    "https://example.com", "http://0.0.0.0:80"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_controller_url(bad)

    def test_expected_symbols_present(self):
        data = json.dumps([{"name": "OPENSIGWEAVE_INJECT"},
                           {"name": "OPENSIGWEAVE_GUARD"}]).encode()
        self.assertEqual(probe("rspamd", "http://10.0.0.9:11334",
                               fetch=lambda _: data)["status"], "configured")

    def test_missing_symbol_not_healthy(self):
        data = json.dumps([{"name": "OPENSIGWEAVE_INJECT"}]).encode()
        self.assertEqual(probe("rspamd", "http://10.0.0.9:11334",
                               fetch=lambda _: data)["status"], "missing")

    def test_unavailable_not_healthy(self):
        def broken(_):
            raise OSError("down")
        self.assertEqual(probe("rspamd", "http://10.0.0.9:11334",
                               fetch=broken)["status"], "unknown")
        self.assertEqual(probe("stalwart", "")["status"], "unsupported")

    def test_mailcow_onboarding_one_command_block(self):
        data = adapter_instructions("mailcow_rspamd")
        self.assertTrue(data["supported"])
        self.assertEqual(data["commands"].count("cd /opt/mailcow-dockerized"), 1)


if __name__ == "__main__":
    unittest.main()
