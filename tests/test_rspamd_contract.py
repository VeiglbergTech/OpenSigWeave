"""Regression guards for security-critical Rspamd adapter contracts.

These source-level checks complement Lua syntax validation. They do not replace
execution against a real Rspamd/Mailcow installation or SMTP/DKIM verification.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "rspamd" / "opensigweave.lua").read_text(encoding="utf-8")


class RspamdAdapterContractTests(unittest.TestCase):
    def test_api_requires_https_and_a_non_placeholder_engine_key(self):
        self.assertRegex(SOURCE, r'API_URL_BASE:match\("\^https://"\)')
        self.assertRegex(SOURCE, r'#ENGINE_API_KEY\s*<\s*32')
        self.assertRegex(SOURCE, r'no_ssl_verify\s*=\s*false')
        self.assertNotRegex(SOURCE, r'no_ssl_verify\s*=\s*true')

    def test_remote_request_limits_are_bounded(self):
        self.assertRegex(SOURCE, r'REQUEST_TIMEOUT\s*=\s*math\.min\(math\.max\(REQUEST_TIMEOUT,\s*0\.5\),\s*15\.0\)')
        self.assertRegex(SOURCE, r'MAX_RESPONSE_SIZE\s*=\s*math\.min\(math\.max\(math\.floor\(MAX_RESPONSE_SIZE\),\s*1024\),\s*1048576\)')
        self.assertRegex(SOURCE, r'timeout\s*=\s*REQUEST_TIMEOUT')
        self.assertRegex(SOURCE, r'max_size\s*=\s*MAX_RESPONSE_SIZE')

    def test_only_authenticated_submission_with_envelope_sender_is_processed(self):
        self.assertRegex(SOURCE, r'local envfrom = task:get_from\(1\)')
        self.assertRegex(SOURCE, r'local uname = task:get_user\(\)')
        self.assertRegex(SOURCE, r'if not envfrom or not uname then return false end')
        self.assertRegex(SOURCE, r'local sender_email = tostring\(envfrom\[1\]\.addr\):lower\(\)')

    def test_control_header_is_single_valued_and_consumed(self):
        self.assertIn("task:get_header_count('X-OpenSigWeave-Mode')", SOURCE)
        self.assertIn("task:modify_header('X-OpenSigWeave-Mode', { remove = { 0 } })", SOURCE)
        self.assertIn("ignoring duplicate mode headers", SOURCE)

    def test_multipart_is_explicitly_left_unchanged(self):
        self.assertIn("if multipart_message then", SOURCE)
        self.assertIn("multipart message left unchanged; safe MIME rewrite is not yet available", SOURCE)
        self.assertRegex(SOURCE, r'if is_multipart then[\s\S]*?return\s+end')
        self.assertIn("Do not rewrite raw multipart bodies with string patterns", SOURCE)

    def test_no_regular_expression_mime_boundary_reconstruction_is_enabled(self):
        # This adapter deliberately refuses multipart messages until a parser-backed
        # rewrite path is implemented and exercised against supported Rspamd versions.
        self.assertRegex(SOURCE, r'if is_multipart then[\s\S]*?return\s+end')
        self.assertNotRegex(SOURCE, r'parse_multipart\s*\(')


if __name__ == "__main__":
    unittest.main()
