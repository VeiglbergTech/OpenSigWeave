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
        self.assertIn('INLINE_IMAGE_MODE == "cid" and 8388608 or 1048576', SOURCE)
        self.assertIn('INLINE_IMAGE_MODE == "cid" and 6291456 or 65536', SOURCE)
        self.assertRegex(SOURCE, r'timeout\s*=\s*REQUEST_TIMEOUT')
        self.assertRegex(SOURCE, r'max_size\s*=\s*MAX_RESPONSE_SIZE')

    def test_only_authenticated_submission_with_envelope_sender_is_processed(self):
        self.assertRegex(SOURCE, r'local envfrom = task:get_from\(1\)')
        self.assertRegex(SOURCE, r'local uname = task:get_user\(\)')
        self.assertIn('type(envfrom[1].addr) ~= "string"', SOURCE)
        self.assertIn('mime_rewriter.preflight(task)', SOURCE)
        self.assertRegex(SOURCE, r'local sender_email = tostring\(envfrom\[1\]\.addr\):lower\(\)')

    def test_deprecated_mode_header_is_only_consumed(self):
        self.assertIn("task:get_header_count('X-OpenSigWeave-Mode')", SOURCE)
        self.assertIn("task:modify_header('X-OpenSigWeave-Mode', { remove = { 0 } })", SOURCE)
        self.assertNotIn("request_headers['X-OpenSigWeave-Mode']", SOURCE)
        self.assertNotIn("requested_mode", SOURCE)

    def test_plaintext_signature_preserves_link_targets(self):
        self.assertIn("html_signature_to_plain", SOURCE)
        self.assertIn('lower_href:sub(1, 7) == "mailto:"', SOURCE)
        self.assertIn('return text .. " (" .. href .. ")"', SOURCE)

    def test_optional_domain_policy_module_does_not_break_legacy_installations(self):
        self.assertIn('if module_options.domain_policy_file ~= nil then', SOURCE)
        self.assertIn('pcall(require, "opensigweave_domain_policy")', SOURCE)
        self.assertIn('domain_policy_unavailable', SOURCE)
        guard = (ROOT / "rspamd" / "opensigweave_guard.lua").read_text(encoding="utf-8")
        self.assertIn('if adapter_options.domain_policy_file ~= nil then', guard)
        self.assertIn('domain_policy_unavailable', guard)

    def test_multipart_uses_rspamd_parsed_mime_tree(self):
        self.assertIn('require "opensigweave_mime"', SOURCE)
        self.assertIn('pcall(mime_rewriter.rewrite, task, function(decoded, is_html)', SOURCE)
        self.assertIn('pcall(task.set_message, task, message)', SOURCE)
        self.assertNotIn('multipart message left unchanged; safe MIME rewrite is not yet available', SOURCE)
        self.assertNotIn('local function process_mime_block(', SOURCE)

    def test_raw_marker_cleanup_cannot_touch_multipart_attachments(self):
        self.assertIn('original_ct and tostring(original_ct):lower():match("^%s*multipart/")', SOURCE)
        self.assertIn('skipping raw marker cleanup for multipart MIME', SOURCE)

    def test_mime_serializer_is_bounded_and_parser_based(self):
        mime = (ROOT / "rspamd" / "opensigweave_mime.lua").read_text(encoding="utf-8")
        self.assertIn('task:get_parts()', mime)
        self.assertIn('part:get_boundary()', mime)
        self.assertIn('part:is_attachment()', mime)
        self.assertIn('raw_utf', mime)
        self.assertIn('signed, encrypted or embedded message', mime)
        self.assertNotIn('parse_multipart(', mime)


if __name__ == "__main__":
    unittest.main()
