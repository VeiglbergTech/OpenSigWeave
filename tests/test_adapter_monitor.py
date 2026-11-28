"""Adapter health and setup contract tests."""
import json
import unittest
from urllib.error import HTTPError
from unittest.mock import patch
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

    def test_real_grouped_rules_response(self):
        # Rspamd /symbols exposes group objects with nested rules.
        payload = [{"name": "local", "rules": [
            {"symbol": "OPENSIGWEAVE_INJECT", "weight": 0.0},
            {"symbol": "OPENSIGWEAVE_GUARD", "weight": 0.0}
        ]}]
        result = probe("rspamd", "http://10.0.0.9:11334",
                       fetch=lambda _: json.dumps(payload).encode())
        self.assertEqual(result["status"], "configured")

    def test_group_name_does_not_count_as_registered_symbol(self):
        payload = [{"name": "OPENSIGWEAVE_INJECT", "rules": [
            {"symbol": "SOME_OTHER_RULE"}
        ]}]
        result = probe("rspamd", "http://10.0.0.9:11334",
                       fetch=lambda _: json.dumps(payload).encode())
        self.assertEqual(result["status"], "unknown")

    def test_controller_auth_required_has_distinct_status(self):
        class DeniedOpener:
            def open(self, request, timeout):
                raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)
        with patch("core.adapter_monitor.build_opener", return_value=DeniedOpener()):
            result = probe("rspamd", "http://10.0.0.9:11334")
        self.assertEqual(result["status"], "auth_required")
        self.assertNotIn("10.0.0.9", result["detail"])

    def test_password_uses_read_only_header(self):
        class FakeResponse:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read(self, length): return json.dumps([
                {"name": "OPENSIGWEAVE_INJECT"}, {"name": "OPENSIGWEAVE_GUARD"}
            ]).encode()
        class PasswordOpener:
            def open(self, request, timeout):
                self.request = request
                return FakeResponse()
        opener = PasswordOpener()
        with patch("core.adapter_monitor.build_opener", return_value=opener):
            result = probe("rspamd", "http://10.0.0.9:11334", password="readonly-secret")
        self.assertEqual(result["status"], "configured")
        self.assertEqual(opener.request.get_header("Password"), "readonly-secret")

    def test_optional_guard_is_not_required_and_missing_injector_fails(self):
        injector_only = json.dumps([{"name": "OPENSIGWEAVE_INJECT"}]).encode()
        result = probe("rspamd", "http://10.0.0.9:11334",
                       fetch=lambda _: injector_only)
        self.assertEqual(result["status"], "configured")
        self.assertIn("optional guard", result["detail"])
        guard_only = json.dumps([{"name": "OPENSIGWEAVE_GUARD"}]).encode()
        self.assertEqual(probe("rspamd", "http://10.0.0.9:11334",
                               fetch=lambda _: guard_only)["status"], "missing")

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
